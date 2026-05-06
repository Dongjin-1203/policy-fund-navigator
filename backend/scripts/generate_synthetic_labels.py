"""합성 레이블 생성 및 α, β, γ 파라미터 추정 스크립트.

실행:
    python scripts/generate_synthetic_labels.py

출력:
    scoring_params.json        — 로컬 저장
    s3://BUCKET/processed/scoring_params.json — S3 업로드
"""
import json
import logging
import os
import sys

import boto3
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sklearn.linear_model import LinearRegression
from sklearn.metrics import r2_score

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.scoring.tools import (
    calc_financial_score,
    calc_policy_score,
    calc_tech_score,
    generate_synthetic_label,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
)
logger = logging.getLogger(__name__)

load_dotenv()


# ── Step 1: S3에서 데이터 로드 ────────────────────────────────────────────────

def load_parquet_from_s3(bucket: str, key: str, local_path: str) -> pd.DataFrame:
    """S3에서 parquet 다운로드 후 DataFrame 반환."""
    s3 = boto3.client('s3')
    logger.info("S3 다운로드: s3://%s/%s -> %s", bucket, key, local_path)
    s3.download_file(bucket, key, local_path)
    df = pd.read_parquet(local_path)
    logger.info("로드 완료: %s shape=%s", local_path, df.shape)
    return df


def inspect_dataframe(df: pd.DataFrame, name: str) -> None:
    """DataFrame 현황 요약 출력."""
    null_pct = (df.isnull().sum() / len(df) * 100).sort_values(ascending=False)
    usable = null_pct[null_pct < 100].index.tolist()
    fully_null = null_pct[null_pct == 100].index.tolist()

    logger.info(
        "%s: shape=%s | 사용가능=%s | 전체null=%s",
        name, df.shape, usable, fully_null,
    )
    if fully_null:
        logger.warning(
            "%s: 재무/기술 컬럼 전체 null (%s) -- 기본값(F=0.5, T=0.0, G=0.0)으로 계산됩니다.",
            name, fully_null,
        )


def main():
    bucket = os.environ.get('S3_BUCKET_NAME')
    if not bucket:
        raise ValueError("S3_BUCKET_NAME 환경변수가 설정되지 않았습니다.")

    # 데이터 로드
    company_df = load_parquet_from_s3(
        bucket, 'processed/company_features.parquet', 'company_features.parquet'
    )
    program_df = load_parquet_from_s3(
        bucket, 'processed/program_features.parquet', 'program_features.parquet'
    )

    inspect_dataframe(company_df, 'company_features')
    inspect_dataframe(program_df, 'program_features')

    # ── Step 2: F·T·G 점수 계산 ───────────────────────────────────────────────
    logger.info("F/T/G 점수 계산 시작 (company 수: %d)", len(company_df))

    score_rows = []
    for _, row in company_df.iterrows():
        company = row.to_dict()
        score_rows.append({
            'company_id': row['company_id'],
            'F': calc_financial_score(company),
            'T': calc_tech_score(company),
            'G': calc_policy_score(company),
        })

    scores_df = pd.DataFrame(score_rows)
    logger.info(
        "점수 통계:  F mean=%.4f std=%.4f | T mean=%.4f std=%.4f | G mean=%.4f std=%.4f",
        scores_df['F'].mean(), scores_df['F'].std(),
        scores_df['T'].mean(), scores_df['T'].std(),
        scores_df['G'].mean(), scores_df['G'].std(),
    )

    # ── Step 3: 합성 레이블 생성 ──────────────────────────────────────────────
    n_pairs = len(company_df) * len(program_df)
    logger.info(
        "합성 레이블 생성 시작 (pairs: %d x %d = %d)",
        len(company_df), len(program_df), n_pairs,
    )

    score_map = scores_df.set_index('company_id')[['F', 'T', 'G']].to_dict('index')

    pairs = []
    for _, company in company_df.iterrows():
        cid = company['company_id']
        s = score_map.get(cid, {'F': 0.5, 'T': 0.0, 'G': 0.0})
        c_dict = company.to_dict()
        for _, program in program_df.iterrows():
            label = generate_synthetic_label(c_dict, program.to_dict())
            pairs.append({
                'company_id': cid,
                'program_id': program['program_id'],
                'label': label,
                'F': s['F'],
                'T': s['T'],
                'G': s['G'],
            })

    labels_df = pd.DataFrame(pairs)

    label_counts = labels_df['label'].value_counts().sort_index()
    label_ratio = labels_df['label'].mean()
    logger.info(
        "레이블 분포: 0(미통과)=%d, 1(통과)=%d, 선정비율=%.2f%%",
        label_counts.get(0, 0), label_counts.get(1, 0), label_ratio * 100,
    )

    # ── Step 4: 선형회귀로 α, β, γ 추정 ──────────────────────────────────────
    X = labels_df[['F', 'T', 'G']].values
    y = labels_df['label'].values

    feature_stds = np.std(X, axis=0)
    logger.info(
        "특징 표준편차 -- F: %.6f, T: %.6f, G: %.6f",
        feature_stds[0], feature_stds[1], feature_stds[2],
    )

    if all(s < 1e-6 for s in feature_stds):
        logger.warning(
            "모든 특징의 분산이 0 (재무/기술 데이터 전체 null). "
            "선형회귀 결과 무의미 -> 초기값(alpha=0.4, beta=0.3, gamma=0.3) 유지."
        )
        alpha, beta, gamma = 0.4, 0.3, 0.3
        r2 = float('nan')
        regression_valid = False
    else:
        model = LinearRegression(positive=True, fit_intercept=False)
        model.fit(X, y)

        raw_coef = model.coef_
        coef_sum = raw_coef.sum()
        if coef_sum > 0:
            alpha, beta, gamma = (raw_coef / coef_sum).tolist()
        else:
            alpha, beta, gamma = 0.4, 0.3, 0.3
            logger.warning("회귀 계수 합이 0 -- 초기값 사용")

        y_pred = model.predict(X)
        r2 = float(r2_score(y, y_pred))
        regression_valid = True

    r2_str = "N/A (데이터 부족)" if (isinstance(r2, float) and np.isnan(r2)) else f"{r2:.4f}"
    logger.info(
        "추정 파라미터: alpha=%.4f, beta=%.4f, gamma=%.4f, sum=%.4f, R2=%s",
        alpha, beta, gamma, alpha + beta + gamma, r2_str,
    )
    logger.info(
        "초기값 비교 -- alpha: 0.4->%.4f, beta: 0.3->%.4f, gamma: 0.3->%.4f",
        alpha, beta, gamma,
    )

    # ── Step 5: 결과 저장 ─────────────────────────────────────────────────────
    results = {
        'alpha': float(alpha),
        'beta': float(beta),
        'gamma': float(gamma),
        'r2_score': None if (isinstance(r2, float) and np.isnan(r2)) else float(r2),
        'label_ratio': float(label_ratio),
        'total_pairs': int(len(labels_df)),
        'total_companies': int(len(company_df)),
        'total_programs': int(len(program_df)),
        'regression_valid': regression_valid,
        'note': (
            "재무/기술 데이터 전체 null로 인해 회귀 불가 -- 도메인 초기값 사용"
            if not regression_valid
            else "선형회귀(positive=True) 추정값 정규화"
        ),
        'generated_at': pd.Timestamp.now().isoformat(),
    }

    with open('scoring_params.json', 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    logger.info("로컬 저장 완료: scoring_params.json")

    s3 = boto3.client('s3')
    s3.upload_file('scoring_params.json', bucket, 'processed/scoring_params.json')
    logger.info("S3 업로드 완료: s3://%s/processed/scoring_params.json", bucket)

    print("\n=== 최종 결과 ===")
    print(json.dumps(results, indent=2))
    return results


if __name__ == '__main__':
    main()
