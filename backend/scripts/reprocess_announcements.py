#!/usr/bin/env python3
"""
reprocess_announcements.py

S3 raw/announcements/ 하위 모든 HWP/PDF 파일을 수정된 processor.py 프롬프트로
강제 재파싱하고 결과를 embeddings/requirements_db/ 에 덮어쓴다.

실행:
    docker compose exec backend python3 scripts/reprocess_announcements.py
    docker compose exec backend python3 scripts/reprocess_announcements.py --dry-run
    docker compose exec backend python3 scripts/reprocess_announcements.py --date 2026-04-29
"""
import argparse
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from datetime import datetime

import boto3

# backend 루트와 src 디렉토리를 import 경로에 추가
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / 'src'))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)-8s %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)

S3_BUCKET = os.environ.get(
    'S3_BUCKET_NAME',
    'policy-fund-nav-2026-388531134598-ap-northeast-2-an',
)
ANNOUNCEMENTS_PREFIX = 'raw/announcements/'
REQUIREMENTS_DB_PREFIX = 'embeddings/requirements_db/'
SUPPORTED_EXTS = {'.hwp', '.pdf'}


# ---------------------------------------------------------------------------
# S3 유틸
# ---------------------------------------------------------------------------

def _s3():
    return boto3.client('s3')


def list_date_folders(s3_client, target_date: str | None = None) -> list[str]:
    """raw/announcements/ 하위 날짜 폴더 목록 반환."""
    paginator = s3_client.get_paginator('list_objects_v2')
    dates: set[str] = set()
    for page in paginator.paginate(
        Bucket=S3_BUCKET, Prefix=ANNOUNCEMENTS_PREFIX, Delimiter='/'
    ):
        for cp in page.get('CommonPrefixes', []):
            date_part = cp['Prefix'].rstrip('/').split('/')[-1]
            if date_part:
                dates.add(date_part)

    sorted_dates = sorted(dates)
    if target_date:
        return [d for d in sorted_dates if d == target_date]
    return sorted_dates


def load_metadata(s3_client, date_str: str) -> list[dict]:
    """raw/announcements/{date}/metadata.json 로드."""
    key = f'{ANNOUNCEMENTS_PREFIX}{date_str}/metadata.json'
    try:
        resp = s3_client.get_object(Bucket=S3_BUCKET, Key=key)
        return json.loads(resp['Body'].read().decode('utf-8'))
    except Exception as exc:
        logger.warning('metadata.json 로드 실패 date=%s: %s', date_str, exc)
        return []


def download_to_temp(s3_client, s3_key: str) -> str | None:
    """S3 파일을 임시 파일로 다운로드. 실패 시 None 반환."""
    suffix = Path(s3_key).suffix.lower() or '.bin'
    try:
        resp = s3_client.get_object(Bucket=S3_BUCKET, Key=s3_key)
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(resp['Body'].read())
            return f.name
    except Exception as exc:
        logger.error('S3 다운로드 실패 key=%s: %s', s3_key, exc)
        return None


def save_json_to_s3(s3_client, data: dict, key: str) -> bool:
    try:
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=key,
            Body=json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8'),
            ContentType='application/json',
        )
        return True
    except Exception as exc:
        logger.error('S3 저장 실패 key=%s: %s', key, exc)
        return False


# ---------------------------------------------------------------------------
# 파싱
# ---------------------------------------------------------------------------

def parse_file(app, file_path: str, slno: str) -> list[dict] | None:
    """
    LangGraph 파이프라인으로 파일 파싱.

    Returns:
        성공 시 flat record 리스트 (1 공고 → N 세부사업).
        실패(검증 불합격 포함) 시 None.
    """
    initial_state = {
        'file_path': file_path,
        'retry_count': 0,
        'is_valid': False,
    }
    try:
        result = app.invoke(initial_state)
    except Exception as exc:
        logger.error('LangGraph 실행 실패 slno=%s: %s', slno, exc)
        return None

    if not result.get('is_valid'):
        logger.warning('검증 실패 (정량 지표 없음 또는 최대 재시도 초과) slno=%s', slno)
        return None

    parsed: dict = result['parsed_json']
    parsed['source_file'] = slno

    announcement_title = parsed.get('announcement_title', '')
    is_amended = parsed.get('is_amended', False)
    programs = parsed.get('programs') or []

    flat: list[dict] = []
    for idx, prog in enumerate(programs):
        flat.append({
            'announcement_title': announcement_title,
            'is_amended': is_amended,
            'source_file': slno,
            'program_id': f'{slno}_{idx}',
            **prog,
        })
    return flat


def _print_sample(record: dict) -> None:
    """핵심 필드 샘플 로그 출력."""
    logger.info('┌─── 샘플 출력 (program_id=%s) ───', record.get('program_id'))
    for field in (
        'announcement_title', 'program_name', 'category',
        'max_support', 'interest_rate',
        'apply_start', 'apply_end',
        'debt_ratio_limit',
    ):
        logger.info('│  %-20s: %s', field, repr(record.get(field)))
    reqs = record.get('requirements') or []
    logger.info('│  %-20s: %d건 — %s', 'requirements', len(reqs),
                repr(reqs[0]) if reqs else '(없음)')
    logger.info('└────────────────────────────────────────')


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

def main(target_date: str | None = None, dry_run: bool = False) -> None:
    # processor.py import (IndustryMapper·RegionMapper 초기화 포함)
    logger.info('processor.py import 중...')
    try:
        from processor import build_parser_graph
    except ImportError as exc:
        logger.error('processor import 실패: %s', exc)
        sys.exit(1)

    logger.info('LangGraph 파이프라인 컴파일 중...')
    app = build_parser_graph()

    s3_client = _s3()

    dates = list_date_folders(s3_client, target_date)
    if not dates:
        logger.warning('처리할 날짜 폴더 없음 (target_date=%s)', target_date)
        return

    logger.info('처리 대상 날짜 폴더: %s', dates)

    total_ok = total_fail = total_skip = 0
    sample_printed = False

    for date_str in dates:
        metadata = load_metadata(s3_client, date_str)
        if not metadata:
            logger.warning('metadata.json 없음 또는 비어 있음: %s', date_str)
            continue

        logger.info('━━━ %s: %d건 처리 시작 ━━━', date_str, len(metadata))

        for item in metadata:
            slno: str = str(item.get('slno', '')).strip()
            filename: str = str(item.get('filename', '')).strip()
            title: str = item.get('title', '')

            if not slno or not filename:
                logger.warning('slno/filename 누락 — 건너뜀: %s', item)
                total_skip += 1
                continue

            ext = Path(filename).suffix.lower()
            if ext not in SUPPORTED_EXTS:
                logger.debug('미지원 확장자 건너뜀: %s', filename)
                total_skip += 1
                continue

            s3_key = f'{ANNOUNCEMENTS_PREFIX}{date_str}/{filename}'
            logger.info('처리 중: slno=%-8s %s', slno, title[:50])

            if dry_run:
                logger.info('[DRY-RUN] 스킵: %s', s3_key)
                continue

            # 다운로드
            tmp_path = download_to_temp(s3_client, s3_key)
            if not tmp_path:
                total_fail += 1
                continue

            # 파싱
            try:
                flat_records = parse_file(app, tmp_path, slno)
            finally:
                try:
                    Path(tmp_path).unlink()
                except OSError:
                    pass

            if flat_records is None:
                total_fail += 1
                continue

            if not flat_records:
                logger.warning('파싱 결과 빈 배열 slno=%s', slno)
                total_fail += 1
                continue

            # S3 저장 (기존 파일 덮어쓰기)
            saved = 0
            for record in flat_records:
                pid = record.get('program_id', f'{slno}_0')
                dest_key = f'{REQUIREMENTS_DB_PREFIX}{date_str}/{pid}.json'
                if save_json_to_s3(s3_client, record, dest_key):
                    saved += 1

            logger.info('저장 완료: slno=%-8s → %d개 사업 → s3://%s/%s%s/%s_*.json',
                        slno, saved, S3_BUCKET, REQUIREMENTS_DB_PREFIX, date_str, slno)
            total_ok += 1

            # 첫 번째 성공 건 샘플 출력
            if not sample_printed:
                _print_sample(flat_records[0])
                sample_printed = True

    logger.info('━━━ 완료 — 성공: %d건, 실패: %d건, 건너뜀: %d건 ━━━',
                total_ok, total_fail, total_skip)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='HWP/PDF 강제 재파싱 스크립트')
    parser.add_argument('--date', help='특정 날짜만 처리 (YYYY-MM-DD)', default=None)
    parser.add_argument('--dry-run', action='store_true', help='S3 접근 없이 목록만 확인')
    args = parser.parse_args()

    main(target_date=args.date, dry_run=args.dry_run)
