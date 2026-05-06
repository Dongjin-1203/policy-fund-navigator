import pandas as pd
import os
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# 경로 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_DATA_PATH = os.path.join(BASE_DIR, '../data/raw/한국행정구역분류_2026.4.1.기준_20260401014049.xlsx')
OUTPUT_PATH = os.path.join(BASE_DIR, '../data/region_master.csv')

def create_region_master_csv():    
    logger.info("한국행정구역분류 데이터 전처리를 시작합니다...")

    try:
        # 파일 존재 여부 확인
        if not os.path.exists(RAW_DATA_PATH):
            logger.error(f"원본 파일을 찾을 수 없습니다: {RAW_DATA_PATH}")
            return
        
        # '2. 항목표(기준시점)' 시트 읽기 (위쪽 2줄의 헤더 설명은 건너뜀)
        df = pd.read_excel(
            RAW_DATA_PATH, 
            sheet_name='2. 항목표(기준시점)', 
            skiprows=2
            )

        # 영문 컬럼명으로 통일
        df.columns = ['null_col', 'sido_code', 'sido_name', 'sigungu_code', 'sigungu_name', 
                    'emd_code', 'emd_name', 'english_name', 'hanja_name', 'note']

        # ---------------------------------------------------------
        # [1단계] 대분류 (시/도) 데이터 추출 (예: 서울특별시, 경기도)
        # ---------------------------------------------------------
        sido_df = df[['sido_code', 'sido_name']].dropna(subset=['sido_code']).drop_duplicates()
        sido_df = sido_df.rename(columns={'sido_name': 'region_name', 'sido_code': 'region_code'})

        # ---------------------------------------------------------
        # [2단계] 중분류 (시/군/구) 데이터 추출 
        # (예: "화성시" -> "경기도 화성시", "중구" -> "서울특별시 중구"로 합쳐서 고유성 확보)
        # ---------------------------------------------------------
        sigungu_df = df[['sido_name', 'sigungu_code', 'sigungu_name']].dropna(subset=['sigungu_code']).drop_duplicates()
        # 시도 이름과 시군구 이름을 합침 (SBERT 임베딩 품질 극대화)
        sigungu_df['region_name'] = sigungu_df['sido_name'] + " " + sigungu_df['sigungu_name']
        sigungu_df = sigungu_df[['sigungu_code', 'region_name']].rename(columns={'sigungu_code': 'region_code'})

        # ---------------------------------------------------------
        # [3단계] 병합 및 CSV 저장
        # ---------------------------------------------------------
        master_df = pd.concat([sido_df, sigungu_df]).drop_duplicates(subset=['region_name'])
        
        # 코드가 소수점(11.0)으로 나오는 것을 방지하기 위해 정수형 문자열로 변환
        master_df['region_code'] = master_df['region_code'].astype(str).str.replace(r'\.0$', '', regex=True)

        # 데이터 폴더에 CSV로 저장
        master_df.to_csv(OUTPUT_PATH, index=False, encoding='utf-8-sig')
        
        logger.info("=" * 50)
        logger.info(f"전처리 완료! 저장 위치: {OUTPUT_PATH}")
        logger.info(f"총 {len(master_df):,}개의 업종 데이터가 정리되었습니다.")
        logger.info("=" * 50)

    except Exception as e:
        logger.exception(f"전처리 과정 중 예상치 못한 에러가 발생했습니다: {e}")

if __name__ == "__main__":
    create_region_master_csv()