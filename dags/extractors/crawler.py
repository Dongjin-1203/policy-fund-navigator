"""중소기업진흥공단(kosmes.or.kr) 정책자금 공고문 크롤러.

수집 대상: 정책자금 융자사업 연간추진계획 게시판 (gubun=WE17) 첨부 HWP/PDF
수집 방식: 세션 획득 → AJAX JSON API → 토큰 기반 POST 다운로드
S3 적재 경로: raw/announcements/YYYY-MM-DD/{파일명}
메타데이터: raw/announcements/YYYY-MM-DD/metadata.json

API 흐름:
  1. GET /nsh/SH/SIT/SHSIT044M0.do  → 세션 쿠키 획득
  2. POST /sh/sit/selectSHSIT032.json (param=proc=Gubun, gubun=WE17) → 게시판 타이틀 검증
  3. POST /sh/sit/selectSHSIT032.json (param=proc=List, gubun=WE17, nowPage=N) → 공고 목록
  4. POST /fileDown2.do (name=토큰, Rname=파일명, path=down) → 파일 바이너리
"""
import json
import logging
import os
import tempfile
import time
from datetime import datetime
from urllib.parse import urljoin

import boto3
import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

KOSMES_BASE_URL = 'https://www.kosmes.or.kr'
SESSION_INIT_PATH = '/nsh/SH/SIT/SHSIT044M0.do'
LIST_API_PATH = '/sh/sit/selectSHSIT032.json'
FILE_DOWNLOAD_PATH = '/fileDown2.do'

# 수집 대상 게시판 코드 → 게시판명
BOARDS = {
    'WE17': '정책자금융자사업연간계획',
}

SUPPORTED_EXTENSIONS = ('.pdf', '.hwp', '.hwpx')
REQUEST_TIMEOUT = 30
REQUEST_DELAY = 0.5


# HWP/HWPX/PDF 매직 바이트
_MAGIC_MAP = {
    b'\xd0\xcf\x11\xe0': 'hwp',      # OLE2 컨테이너 (HWP 97~2007)
    b'HWP ': 'hwpx',                  # HWP Document File (hwpx 일부)
    b'%PDF': 'pdf',
}


def _detect_file_format(content: bytes) -> str:
    """매직 바이트로 파일 형식을 판별한다. 알 수 없으면 'unknown' 반환."""
    for magic, fmt in _MAGIC_MAP.items():
        if content[:len(magic)] == magic:
            return fmt
    # HWPX는 ZIP 기반 (PK\x03\x04)
    if content[:4] == b'PK\x03\x04':
        return 'hwpx'
    return 'unknown'


def _get_s3_client():
    return boto3.client('s3')


def _get_bucket() -> str:
    bucket = os.environ.get('S3_BUCKET_NAME')
    if not bucket:
        raise ValueError('S3_BUCKET_NAME 환경변수가 설정되지 않았습니다.')
    return bucket


def _s3_key_exists(s3_client, bucket: str, key: str) -> bool:
    try:
        s3_client.head_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False


def _make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/124.0.0.0 Safari/537.36'
        ),
        'Accept-Language': 'ko-KR,ko;q=0.9',
    })
    return session


def init_session(session: requests.Session) -> None:
    """페이지를 방문하여 JSESSIONID 세션 쿠키를 획득한다."""
    url = urljoin(KOSMES_BASE_URL, SESSION_INIT_PATH)
    resp = session.get(url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    logger.info('세션 초기화 완료 (cookies=%s)', list(session.cookies.keys()))


def fetch_announcement_page(
    session: requests.Session,
    gubun: str,
    page: int = 1,
    page_count: int = 10,
) -> dict:
    """공고 목록 한 페이지를 JSON API로 가져온다.

    Args:
        session: 세션 쿠키가 있는 requests.Session
        gubun: 게시판 코드 (예: 'WE17')
        page: 페이지 번호 (1부터 시작)
        page_count: 페이지당 건수

    Returns:
        {'ds_infoList': [...], 'pageInfo': {...}}
    """
    url = urljoin(KOSMES_BASE_URL, LIST_API_PATH)
    data = {
        'nowPage': str(page),
        'pageCount': str(page_count),
        'rowCount': str(page_count),
        'param': 'proc=List',
        'page': gubun,
        'gubun': gubun,
    }
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8',
        'X-Requested-With': 'XMLHttpRequest',
        'Referer': urljoin(KOSMES_BASE_URL, SESSION_INIT_PATH),
    }
    resp = session.post(url, data=data, headers=headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def fetch_all_announcements(session: requests.Session, gubun: str) -> list[dict]:
    """게시판 전체 페이지를 순회하여 공고 목록을 수집한다.

    Returns:
        각 공고별 딕셔너리 리스트:
        [{'title', 'filename', 'token', 'date', 'slno', 'gubun'}, ...]
    """
    all_items: list[dict] = []
    page = 1

    while True:
        time.sleep(REQUEST_DELAY)
        try:
            result = fetch_announcement_page(session, gubun, page=page)
        except Exception as exc:
            logger.error('목록 조회 실패 (gubun=%s, page=%d): %s', gubun, page, exc)
            break

        raw_items = result.get('ds_infoList', [])
        if not raw_items:
            break

        for item in raw_items:
            filename = item.get('FL_NM', '') or ''
            token = item.get('DCLR_DATA_FL_MSK_TXT', '') or ''
            _, ext = os.path.splitext(filename.lower())
            if ext not in SUPPORTED_EXTENSIONS:
                logger.debug('지원하지 않는 확장자 스킵: %s', filename)
                continue
            all_items.append({
                'title': item.get('TITL_NM', ''),
                'filename': filename,
                'token': token,
                'date': item.get('REG_DTM', ''),
                'slno': str(item.get('SLNO', '')),
                'gubun': gubun,
            })

        page_info = result.get('pageInfo', {})
        max_page = page_info.get('maxPage') or page_info.get('endPage') or 1
        logger.info('목록 조회 (gubun=%s, page=%d/%s): %d건', gubun, page, max_page, len(raw_items))

        if page >= int(max_page):
            break
        page += 1

    logger.info('전체 수집 완료 (gubun=%s): %d건', gubun, len(all_items))
    return all_items


def download_file(session: requests.Session, token: str, filename: str) -> bytes:
    """토큰 기반으로 파일을 POST 다운로드한다."""
    url = urljoin(KOSMES_BASE_URL, FILE_DOWNLOAD_PATH)
    data = {'name': token, 'Rname': filename, 'path': 'down'}
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8',
        'Referer': urljoin(KOSMES_BASE_URL, SESSION_INIT_PATH),
    }
    last_exc = None
    for attempt in range(1, 4):
        try:
            resp = session.post(url, data=data, headers=headers, timeout=60, stream=True)
            resp.raise_for_status()
            content = resp.content
            if len(content) < 100:
                raise ValueError(f'다운로드 응답이 너무 짧습니다 ({len(content)} bytes)')
            return content
        except Exception as exc:
            last_exc = exc
            logger.warning('파일 다운로드 실패 (시도 %d/3) %s: %s', attempt, filename, exc)
            if attempt < 3:
                time.sleep(2 ** attempt)
    raise RuntimeError(f'파일 다운로드 최대 재시도 초과: {filename}') from last_exc


def upload_file_to_s3(s3_client, content: bytes, bucket: str, key: str) -> None:
    ext = os.path.splitext(key)[1].lower()
    content_type_map = {
        '.pdf': 'application/pdf',
        '.hwp': 'application/x-hwp',
        '.hwpx': 'application/x-hwpx',
    }
    content_type = content_type_map.get(ext, 'application/octet-stream')
    s3_client.put_object(Bucket=bucket, Key=key, Body=content, ContentType=content_type)
    logger.info('S3 업로드 완료: s3://%s/%s', bucket, key)


def upload_metadata_to_s3(s3_client, metadata: list[dict], bucket: str, date_str: str) -> None:
    """새 메타데이터를 기존 metadata.json에 병합한 뒤 S3에 저장한다.

    병합 키는 slno(공고번호). skipped=True 항목은 기존 수집 데이터를 우선하여
    file_size, file_format 등이 유실되지 않도록 한다.
    """
    key = f'raw/announcements/{date_str}/metadata.json'

    existing: dict[str, dict] = {}
    try:
        resp = s3_client.get_object(Bucket=bucket, Key=key)
        for item in json.loads(resp['Body'].read().decode('utf-8')):
            slno = item.get('slno')
            if slno:
                existing[slno] = item
    except s3_client.exceptions.NoSuchKey:
        pass
    except Exception as exc:
        logger.warning('기존 메타데이터 로드 실패 — 새로 생성합니다: %s', exc)

    for item in metadata:
        slno = item.get('slno')
        if not slno:
            continue
        if item.get('skipped') and slno in existing:
            # 스킵 항목은 기존 수집 데이터(file_size, file_format 등)를 유지
            existing[slno] = existing[slno]
        else:
            existing[slno] = item

    merged = list(existing.values())
    body = json.dumps(merged, ensure_ascii=False, indent=2).encode('utf-8')
    s3_client.put_object(Bucket=bucket, Key=key, Body=body, ContentType='application/json')
    logger.info('메타데이터 업로드 완료: s3://%s/%s (%d건)', bucket, key, len(merged))


class KosmesAnnouncementCrawler:
    """중소기업진흥공단 정책자금 공고문 크롤러.

    run() 메서드를 호출하면 정책자금 공고 목록을 수집하고 첨부파일을 S3에 업로드한다.
    이미 수집된 파일은 S3 존재 여부를 확인하고 스킵한다.
    """

    def __init__(self, mock: bool = False):
        self.mock = mock
        self.date_str = datetime.now().strftime('%Y-%m-%d')
        self._s3 = None
        self._bucket = None

    def _init_s3(self) -> None:
        if self._s3 is None:
            self._s3 = _get_s3_client()
            self._bucket = _get_bucket()

    def _collect_announcements(self, session: requests.Session) -> list[dict]:
        if self.mock:
            logger.info('[MOCK] 공고 목록 반환')
            return [
                {
                    'title': '[mock] 2026년도 정책자금 융자사업 연간추진계획',
                    'filename': 'mock_2026_policy_fund.hwp',
                    'token': '9999999999999',
                    'date': self.date_str,
                    'slno': '999',
                    'gubun': 'WE17',
                }
            ]

        init_session(session)

        all_items: list[dict] = []
        for gubun, board_name in BOARDS.items():
            logger.info('게시판 수집 시작: %s (%s)', board_name, gubun)
            items = fetch_all_announcements(session, gubun)
            all_items.extend(items)

        return all_items

    def _process_announcement(
        self,
        announcement: dict,
        session: requests.Session,
    ) -> dict | None:
        filename = announcement['filename']
        token = announcement['token']
        title = announcement['title']
        slno = announcement.get('slno', '')
        # slno를 접두사로 붙여 JOIN 키를 파일명에 고정
        saved_filename = f'{slno}_{filename}' if slno else filename
        s3_key = f'raw/announcements/{self.date_str}/{saved_filename}'

        if _s3_key_exists(self._s3, self._bucket, s3_key):
            logger.info('스킵 (이미 수집됨): %s', s3_key)
            return {
                'title': title,
                'filename': saved_filename,
                'slno': slno,
                'token': token,
                'date': announcement['date'],
                'gubun': announcement['gubun'],
                's3_path': f's3://{self._bucket}/{s3_key}',
                'collected_at': datetime.now().isoformat(),
                'skipped': True,
            }

        if self.mock:
            logger.info('[MOCK] 다운로드 스킵: %s', saved_filename)
            return {
                'title': title,
                'filename': saved_filename,
                'slno': slno,
                'token': token,
                'date': announcement['date'],
                'gubun': announcement['gubun'],
                's3_path': f's3://{self._bucket}/{s3_key}',
                'collected_at': datetime.now().isoformat(),
                'skipped': False,
                'mock': True,
            }

        tmp_path = None
        uploaded = False
        try:
            time.sleep(REQUEST_DELAY)
            content = download_file(session, token, filename)

            # 로컬 임시 저장 및 형식 검증
            suffix = os.path.splitext(filename)[1] or '.bin'
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(content)
                tmp_path = tmp.name

            file_size = len(content)
            file_format = _detect_file_format(content)
            logger.info(
                '로컬 저장 완료: %s | 크기=%d bytes | 형식=%s',
                tmp_path, file_size, file_format,
            )

            if file_format == 'unknown':
                logger.warning('알 수 없는 파일 형식 — 그대로 업로드: %s', filename)

            upload_file_to_s3(self._s3, content, self._bucket, s3_key)
            uploaded = True

            return {
                'title': title,
                'filename': saved_filename,
                'slno': slno,
                'token': token,
                'date': announcement['date'],
                'gubun': announcement['gubun'],
                's3_path': f's3://{self._bucket}/{s3_key}',
                'collected_at': datetime.now().isoformat(),
                'file_size': file_size,
                'file_format': file_format,
                'skipped': False,
            }
        except Exception as exc:
            logger.error('파일 처리 실패 (%s): %s', filename, exc)
            if tmp_path and os.path.exists(tmp_path) and not uploaded:
                logger.info('S3 업로드 실패 — 로컬 파일 유지: %s', tmp_path)
            return None
        finally:
            # S3 업로드 성공한 경우에만 임시 파일 삭제
            if uploaded and tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def run(self, limit: int | None = None) -> list[dict]:
        """크롤링 메인 실행.

        Args:
            limit: 처리할 최대 공고 건수. None이면 전체 처리.

        Returns:
            수집된 파일별 메타데이터 리스트.
        """
        self._init_s3()
        session = _make_session()

        announcements = self._collect_announcements(session)
        if not announcements:
            logger.warning('수집된 공고가 없습니다.')
            return []

        if limit is not None:
            announcements = announcements[:limit]

        logger.info('처리 대상 공고: %d건', len(announcements))
        metadata: list[dict] = []

        for announcement in announcements:
            result = self._process_announcement(announcement, session)
            if result:
                metadata.append(result)

        upload_metadata_to_s3(self._s3, metadata, self._bucket, self.date_str)
        logger.info('크롤링 완료: 총 %d건', len(metadata))
        return metadata


def run_crawling(mock: bool = False, limit: int | None = None) -> None:
    """메인 실행 함수."""
    crawler = KosmesAnnouncementCrawler(mock=mock)
    crawler.run(limit=limit)


def extract_announcements_task(**context) -> None:
    """Airflow PythonOperator에서 호출 가능한 callable."""
    run_crawling()


if __name__ == '__main__':
    import argparse
    from dotenv import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser(description='중진공 정책자금 공고문 크롤러')
    parser.add_argument('--mock', action='store_true', help='mock 모드 (실제 HTTP 요청 없이 구조 검증)')
    parser.add_argument('--limit', type=int, default=None, help='처리할 최대 공고 건수 (기본값: 전체)')
    args = parser.parse_args()
    run_crawling(mock=args.mock, limit=args.limit)
