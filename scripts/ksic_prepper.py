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
RAW_DATA_PATH = os.path.join(BASE_DIR, '../data/raw/7.한국표준산업분류 제11차 개정 분류체계(대-중-소-세-세세 구분) _20240626043559.xlsx')
OUTPUT_PATH = os.path.join(BASE_DIR, '../data/ksic_master.csv')

def prepare_ksic_data():
    logger.info("KSIC 데이터 전처리를 시작합니다...")

    try:
        # 파일 존재 여부 확인
        if not os.path.exists(RAW_DATA_PATH):
            logger.error(f"원본 파일을 찾을 수 없습니다: {RAW_DATA_PATH}")
            return
    
        # 엑셀 읽기
        # 상단 3줄(제목, 분류명, 코드/항목명 헤더)은 무시하고 4행부터 읽기
        df = pd.read_excel(
            RAW_DATA_PATH, 
            sheet_name='11차개정한국표준산업분류',
            skiprows=3,  # 상단 3줄 건너뛰기
            header=None  # 직접 이름을 붙일 것이므로 헤더 없음으로 설정
    )
    
        # 컬럼명 지정 (10개)
        df.columns = [
            'section_code', 'section_name', 
            'division_code', 'division_name',
            'group_code', 'group_name',
            'class_code', 'class_name',
            'subclass_code', 'subclass_name'
        ]
        
        # 결측치 채우기 (Forward Fill)
        logger.info("데이터 정제 및 병합 셀(NaN) 채우기 진행 중...")
        df_filled = df.ffill()
        
        # 유효성 검사
        # 세세분류 코드(subclass_code)가 없는 행은 데이터가 아니므로 제거합니다.
        df_final = df_filled.dropna(subset=['subclass_code'])
        
        # 저장 (utf-8-sig는 엑셀에서 열었을 때 한글 안 깨지게 해줌)
        df_final.to_csv(OUTPUT_PATH, index=False, encoding='utf-8-sig')
        
        logger.info("=" * 50)
        logger.info(f"전처리 완료! 저장 위치: {OUTPUT_PATH}")
        logger.info(f"총 {len(df_final):,}개의 업종 데이터가 정리되었습니다.")
        logger.info("=" * 50)

    except Exception as e:
        logger.exception(f"전처리 과정 중 예상치 못한 에러가 발생했습니다: {e}")

if __name__ == "__main__":
    prepare_ksic_data()