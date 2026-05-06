import logging
import os
import shutil
import json
import re
import pandas as pd
from datetime import datetime

from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

class PolicyVectorStore:
    # 결측치 기본값 상수
    MAX_REVENUE = 10**15  # 1,000조
    MAX_AGE = 999
    MAX_EMPLOYEES = 10**7
    MAX_DEBT_RATIO = 999999.0
    MAX_EXPORT_USD = 10**12 # 1조 달러

    def __init__(self, persist_directory="./chroma_db", reset=False):
        # 임베딩 모델 세팅
        logger.info("임베딩 모델 로딩 중... (snunlp/KR-SBERT-V40K-klueNLI-augSTS)")
        self.embeddings = HuggingFaceEmbeddings(
            model_name="snunlp/KR-SBERT-V40K-klueNLI-augSTS",
            model_kwargs={'device': 'cpu'},
            encode_kwargs={'normalize_embeddings': True}
        )

        # 로컬 ChromaDB 초기화
        self.persist_directory = persist_directory

        # reset=True이면 기존 데이터 지우기
        if reset and os.path.exists(self.persist_directory):
            shutil.rmtree(self.persist_directory)
            logger.warning(f"기존 DB({self.persist_directory})를 초기화했습니다.")

        self.db = Chroma(
            collection_name="policy",
            embedding_function=self.embeddings,
            persist_directory=self.persist_directory,
            collection_metadata={"hnsw:space": "cosine"} # 코사인 유사도로 강제 지정
        )
        
    def add_policies(self, df: pd.DataFrame, batch_size=100):
        """processor.py에서 파싱된 데이터프레임을 받아서 Vector DB에 저장"""
        # NaN 값을 각 타입에 맞는 기본값으로 미리 채우기
        df = df.copy()

        # processor.py의 최신 필드명 및 validate_node에서 파생된 리스트 필드 총망라
        list_columns = [
            'target_company_types', 'target_industry_text', 'target_industry_codes',
            'excluded_industry_text', 'excluded_industry_codes', 'excluded_subset_condition',
            'target_ksic_codes', 'target_ksic_sections', 'excluded_ksic_codes', 'excluded_ksic_sections',
            'conditional_excluded_ksic_infos', 'region_raw', 'mapped_region_codes', 'special_zones',
            'requirements', 'technical_terms', 'caution_notes', 'support_description'
        ]
        
        for col in list_columns:
            if col in df.columns:
                df[col] = df[col].apply(
                    lambda x: json.loads(x) if isinstance(x, str) and x.startswith('[') else (x if isinstance(x, list) else [])
                )

        # 결측치 기본값 채우기
        fill_rules = {
            'max_support': 0,
            'debt_ratio_limit': self.MAX_DEBT_RATIO,
            'min_business_age': 0,
            'max_business_age': self.MAX_AGE,
            'min_revenue': 0,
            'max_revenue': self.MAX_REVENUE,
            'min_export_usd': 0, 
            'max_export_usd': self.MAX_EXPORT_USD,
            'min_employees': 0,
            'max_employees': self.MAX_EMPLOYEES
        }

        for col, val in fill_rules.items():
            if col in df.columns:
                df[col] = df[col].fillna(val)
            else:
                df[col] = val

        documents = []
        today = datetime.now().strftime('%Y-%m-%d')
        for _, row in df.iterrows():
            # 내부 헬퍼 함수
            def get_list_str(column_name, default_msg="정보 없음"):
                items = row.get(column_name)
                # 리스트 형태이면서 내용이 있는 경우에만 조인
                if isinstance(items, list) and len(items) > 0:
                    return ", ".join(map(str, items))
                return default_msg

            title = str(row.get('program_name', '제목없음'))
            category = str(row.get('category', '기타'))
            company_types = get_list_str('target_company_types', "형태 무관")

            target_ind = get_list_str('target_industry_text', "업종 제한 없음")
            excluded = get_list_str('excluded_industry_text', "없음")
            excluded_subset_cond = get_list_str('excluded_subset_condition', "")
            support_desc = get_list_str('support_description', '정보 없음')
            req = get_list_str('requirements', "")
            tech = get_list_str('technical_terms', "")
            caution = get_list_str('caution_notes', '없음')

            max_support = int(row.get('max_support', 0))
            i_rate = str(row.get('interest_rate', '정보 없음'))
            
            p_id = str(row.get('program_id', '정보 없음'))
            is_amended = str(row.get('is_amended', False))
            a_title = str(row.get('announcement_title', '정보 없음'))

            region = get_list_str('region_raw', "전국")

            min_age = int(row.get('min_business_age', 0))
            max_age = int(row.get('max_business_age', self.MAX_AGE))
            min_revenue = int(row.get('min_revenue', 0))
            max_revenue = int(row.get('max_revenue', self.MAX_REVENUE))
            min_export = int(row.get('min_export_usd', 0))
            max_export = int(row.get('max_export_usd', self.MAX_EXPORT_USD))
            min_employees = int(row.get('min_employees', 0))
            max_employees = int(row.get('max_employees', self.MAX_EMPLOYEES))                          
            debt_limit = float(row.get('debt_ratio_limit', self.MAX_DEBT_RATIO))
            apply_start = str(row.get('apply_start', '1900-01-01'))
            apply_end = str(row.get('apply_end', '9999-12-31'))
            source_date = str(row.get('source_date', today))

            # 임베딩용 텍스트 구성
            page_content = (
                f"공고명: {a_title}\n"
                f"사업명: {title}\n"
                f"카테고리(분류): {category}\n"
                f"기업형태: {company_types}\n"
                f"지원대상산업: {target_ind}\n"
                f"제외업종: {excluded}\n"
                f"조건부 지원 제외 사항: {excluded_subset_cond}\n"
                f"지역: {region}\n"
                f"업력: 최소 {min_age}년 이상, 최대 {max_age}년 이하\n"
                f"매출액: 최소 {min_revenue}원 이상, 최대 {max_revenue}원 이하\n"
                f"수출액: 최소 {min_export}USD 이상, 최대 {max_export}USD 이하\n"
                f"근로자수: 최소 {min_employees}명 이상, 최대 {max_employees}명 이하\n"
                f"부채비율: 최대 {debt_limit}%\n"
                f"자격요건: {req}\n"
                f"기술 키워드: {tech}\n"
                f"지원금액: 최대 {max_support}원 지원 (금리: {i_rate})\n"
                f"지원내용 설명: {support_desc}\n"
                f"주의사항: {caution}\n"
                )

            # 메타데이터에 원본 정보 저장
            metadata = {
                "program_id": p_id,
                "is_amended": is_amended,
                "announcement_title": a_title,
                "category": category,
                "target_company_types": json.dumps(row.get('target_company_types', [])),

                "target_ksic_codes": json.dumps(row.get('target_ksic_codes', [])),
                "target_ksic_sections": json.dumps(row.get('target_ksic_sections', [])),
                "excluded_ksic_codes": json.dumps(row.get('excluded_ksic_codes', [])),
                "excluded_ksic_sections": json.dumps(row.get('excluded_ksic_sections', [])),

                "conditional_excluded_ksic_infos": json.dumps(row.get('conditional_excluded_ksic_infos', [])),
                "caution_notes": json.dumps(row.get('caution_notes', [])),

                "region_raw": json.dumps(row.get('region_raw', [])),
                "mapped_region_codes": json.dumps(row.get('mapped_region_codes', [])),
                "max_support": max_support,
                "min_business_age": min_age,
                "max_business_age": max_age,
                "min_revenue": min_revenue,
                "max_revenue": max_revenue,

                "min_export_usd": row['min_export_usd'],
                "max_export_usd": row['max_export_usd'],

                "min_employees": min_employees,
                "max_employees": max_employees,
                "debt_ratio_limit": debt_limit,
                "interest_rate": i_rate,
                "apply_start": apply_start,
                "apply_end": apply_end,
                "source_date": source_date
            }
            documents.append(Document(page_content=page_content, metadata=metadata))

        # Batch 저장
        for i in range(0, len(documents), batch_size):
            batch = documents[i : i + batch_size]
            self.db.add_documents(batch)
            logger.info(f"Batch 저장 완료: {i + len(batch)} / {len(documents)}")

    def _soft_filter(self, query: str, top_k: int = 10, threshold: float = 0.8) -> list[Document]:
        """Soft Filter: 의미적 유사도 검색"""
        logger.debug(f"검색어: '{query}' (임계값: {threshold})")

        # 코사인 유사도 점수와 함꼐 검색
        results = self.db.similarity_search_with_relevance_scores(query, k=top_k)
        
        passed_docs = []
        for doc, score in results:
            if score >= threshold:
                passed_docs.append((doc, score))

        logger.info(f"결과: 총 {len(results)}개 검색됨, 그 중 Soft Filter({threshold}) 통과: {len(passed_docs)}개")
        return passed_docs
    
    def _hard_filter(self, user_profile: dict, candidates: list):
        """Hard Filter: 자격 요건 검증"""
        logger.info("Hard Filter 자격 요건 검증 시작")
        today = datetime.now()

        green_pass = []  # 통과(모든 정량적 조건을 충족)
        yellow_pending = []  # 조건부 반려(노력을 통해 조정 가능하거나 시간이 해결해주는 사유)
        red_fail = []  # 즉시 탈락(당장 바꿀 수 없는 결격 사유)

        # 사용자 프로필 정보 (None 대비 기본값 설정)
        u_type = user_profile.get('company_type', '')
        u_ind_code = user_profile.get('industry_code', '')
        u_ind_sec = user_profile.get('industry_section', '')
        u_region = user_profile.get('region', '')
        u_age = user_profile.get('business_age', 0)
        u_rev = user_profile.get('revenue', 0)
        u_exp_usd = user_profile.get('export_usd', 0)
        u_emp = user_profile.get('employees', 0)
        u_debt = user_profile.get('debt_ratio', None)

        for doc, score in candidates:
            meta = doc.metadata
            yellow_reasons = []
            red_reasons = []

            # ----- RED: 수정 불가능 -----
            # 기업 형태
            meta_types = json.loads(meta.get('target_company_types', '[]'))
            if meta_types and u_type and not any(t in u_type for t in meta_types):
                red_reasons.append(f"company_type_mismatch (Required: {meta_types})")

            # 대상 업종 (industry_major_category)
            meta_target_codes = json.loads(meta.get('target_ksic_codes', '[]'))
            meta_target_sections = json.loads(meta.get('target_ksic_sections', '[]'))
            if meta_target_codes or meta_target_sections:
                is_target_matched = False
                if u_ind_sec and u_ind_sec in meta_target_sections:
                    is_target_matched = True
                if u_ind_code and any(u_ind_code.startswith(str(c)) for c in meta_target_codes):
                    is_target_matched = True
                
                if not is_target_matched:
                    red_reasons.append("industry_mismatch (지원 대상 업종 아님)")

            # 제외 업종 (industry_limit)
            meta_excluded_codes = json.loads(meta.get('excluded_ksic_codes', '[]'))
            meta_excluded_sections = json.loads(meta.get('excluded_ksic_sections', '[]'))
            if u_ind_sec and u_ind_sec in meta_excluded_sections:
                red_reasons.append("industry_limit_hit (제외 대분류에 해당)")
            if u_ind_code and any(u_ind_code.startswith(str(c)) for c in meta_excluded_codes):
                red_reasons.append("industry_limit_hit (제외 세부코드에 해당)")

            # 지역 (region) 불일치
            meta_region = json.loads(meta.get('region_raw', '["전국"]'))
            if meta_region != '전국' and u_region and not any(u_region in r for r in meta_region):
                red_reasons.append(f"region_mismatch (Required: {meta_region})")

            # 업력(max_buisness_age) 초과
            meta_max_age = meta.get('max_business_age', self.MAX_AGE)
            if u_age > meta_max_age:
                red_reasons.append("age_excess")

            # 업력(min_business_age) 대폭(2년 이상) 미달
            meta_min_age = meta.get('min_business_age', 0)
            if u_age < meta_min_age - 1:
                red_reasons.append("age_shortage_critical")

            # 매출액(max_revenue) 초과
            meta_max_rev = meta.get('max_revenue', self.MAX_REVENUE)
            if u_rev > meta_max_rev:
                red_reasons.append("revenue_excess")

            # 매출액(min_revenue) 대폭 미달 (요건의 80% 미만)
            meta_min_rev = meta.get('min_revenue', 0)
            if u_rev < meta_min_rev * 0.8:
                red_reasons.append("revenue_shortage_critical")

            # 수출액 USD 대폭 미달 (요건의 80% 미만)
            meta_min_exp = meta.get('min_export_usd', 0)
            if u_exp_usd < meta_min_exp * 0.8: 
                red_reasons.append("export_shortage_critical")

            # 근로자수(max_employees) 초과
            meta_max_emp = meta.get('max_employees', self.MAX_EMPLOYEES)
            if u_emp > meta_max_emp:
                red_reasons.append("employees_excess")

            # 근로자수(min_employees) 대폭(3명 이상) 미달
            meta_min_emp = meta.get('min_employees', 0)
            if u_emp < meta_min_emp - 2:
                red_reasons.append("employees_shortage_critical")

            # 부채비율 (debt_ratio_limit) 대폭(10% 넘게) 초과
            meta_debt = meta.get('debt_ratio_limit', self.MAX_DEBT_RATIO)
            if u_debt is not None and u_debt > meta_debt * 1.10:
                red_reasons.append("debt_excess_critical")

            # 마감일(apply_end) 지남
            meta_end_str = meta.get('apply_end', '9999-12-31')
            try: 
                meta_end = datetime.strptime(meta_end_str, '%Y-%m-%d')
                if meta_end < today:
                    red_reasons.append(f"deadline_passed")
            except ValueError:
                logger.error(f"날짜 포맷 오류 발생: {meta_end_str}")

            # ----- YELLOW: 조건부 대기 (수치 조정이 가능) ---
            # 업력 (min_business_age) 소폭(1년) 미달
            if u_age == meta_min_age - 1:
                yellow_reasons.append("age_shortage_minor")

            # 매출액(min_revenue) 소폭 미달 (요건의 80% 이상)
            if meta_min_rev * 0.8 <= u_rev < meta_min_rev:
                yellow_reasons.append("revenue_shortage_minor")

            # 수출액 USD 소폭 미달 (요건의 80% 이상)
            if meta_min_exp * 0.8 <= u_exp_usd < meta_min_exp: 
                yellow_reasons.append("export_shortage_minor")

            # 근로자수(min_employees) 소폭(2명 이하) 미달
            if meta_min_emp -2 <= u_emp < meta_min_emp:
                yellow_reasons.append("employees_shortage_minor")

            # 부채비율 (debt_ratio_limit) 소폭(10% 이하) 초과
            if u_debt is not None and meta_debt < u_debt <= meta_debt * 1.10:
                yellow_reasons.append("debt_excess_minor")

            # 접수 예정 (apply_start)
            meta_start_str = meta.get('apply_start', '1900-01-01')
            try:
                meta_start = datetime.strptime(meta_start_str, '%Y-%m-%d')
                if today < meta_start:
                    yellow_reasons.append(f"not_started_yet")
            except ValueError:
                logger.error(f"날짜 포맷 오류 발생: {meta_start_str}")

            # ----- info (참고사항) -----
            conditions = json.loads(meta.get('conditional_excluded_ksic_infos', '[]'))
            caution_notes = json.loads(meta.get('caution_notes', '[]'))
            if u_ind_code:
                for cond in conditions:
                    if u_ind_code.startswith(str(cond['code'])):
                        caution_notes.append(f"※ 조건부 제외 확인: 귀사의 업종 코드({cond['code']}) 중 '{cond['condition']}'에 해당하는 경우 지원이 불가할 수 있습니다.")

            # 분류
            result_item = {
                "doc": doc,
                "score": score,
                "reasons": red_reasons + yellow_reasons,
                "meta_data": meta,
                "caution_notes": caution_notes,
            }

            if red_reasons:
                red_fail.append(result_item)
            elif yellow_reasons:
                yellow_pending.append(result_item)
            else:
                green_pass.append(result_item)
        
        logger.info(f"검증 완료 - 통과: {len(green_pass)}, 대기: {len(yellow_pending)}, 탈락: {len(red_fail)}")
        return {"green": green_pass, "yellow": yellow_pending, "red": red_fail}
    
    def search_for_agent(self, user_profile: dict, query: str, top_k=10):
        """인터페이스"""
        logger.info(f"에이전트 검색 시작 - Query: {query}")
        # Soft
        raw_results = self._soft_filter(query, top_k=top_k, threshold=0.2)
        
        # Hard
        return self._hard_filter(user_profile, raw_results)

    
if __name__ == "__main__":
    # 테스트용 가상 데이터 (나중에 processor.py 결과로 교체)
    dummy_data = [
{
            # 케이스 1: 표준적인 기술 지원 사업 (모든 필드 충만)
            "program_id": "9999_1",
            "announcement_title": "2026년도 인공지능 고도화 지원사업 공고",
            "program_name": "AI 기반 SaaS 도구 개발 지원",
            "category": "기술",
            "target_industry_text": ["소프트웨어 개발 및 공급업", "컴퓨터 프로그래밍 서비스업"],
            "target_ksic_sections": ["J"],
            "excluded_industry_text": ["유흥주점업", "사행시설 관리업"],
            "region_raw": ["전국", "서울"],
            "max_support": 100000000,
            "min_business_age": 1,
            "max_business_age": 7,
            "min_revenue": 0,
            "max_revenue": 5000000000,
            "min_employees": 0,
            "max_employees": 50,
            "requirements": ["세금 체납이 없는 기업", "AI 기술 보유 기업"],
            "technical_terms": ["AI", "LLM", "Cloud Native", "RAG"],
            "debt_ratio_limit": 1000.0,
            "interest_rate": None,
            "apply_start": "2026-05-01",
            "apply_end": "2026-06-30",
            "is_amended": False,
            "source_date": "2026-05-01"
        },
        {
            # 케이스 2: 세부업종이 비어있는 경우 (Fallback 로직 테스트용)
            "program_id": "8888_1",
            "announcement_title": "제조업 스마트 공장 보급 확산 사업",
            "program_name": "제조현장 디지털 전환(DX) 지원",
            "category": "기술",
            "target_industry_text": ["제조업", "정보통신업"],
            "target_ksic_section": ["C", "J"],
            "excluded_industry_text": [],
            "region_raw": ["부산", "경남"],
            "max_support": 50000000,
            "min_business_age": 3, # Yellow 사유 1
            "max_business_age": None,
            "requirements": ["스마트공장 도입 희망 기업"],
            "technical_terms": ["IoT", "Digital Twin"],
            "debt_ratio_limit": 550.0, # Yellow 사유 2 (10% 이내 초과)
            "interest_rate": "연 2.5% 고정금리",
            "apply_start": "2026-04-15",
            "apply_end": "9999-12-31",
            "is_amended": True
        },
        {
            # 케이스 3: 금융/융자 사업 (수치 데이터 및 부채비율 테스트용)
            "program_id": "7777_1",
            "announcement_title": "소상공인 특별 경영안정자금 융자 계획",
            "program_name": "재난피해 소상공인 긴급대출",
            "category": "금융",
            "target_industry_text": ["음식점업", "의류 도매업"],
            "excluded_industry_text": ["금융업", "보험업"],
            "region_raw": ["부산"],
            "max_support": 30000000,
            "requirements": [],
            "technical_terms": ["경영안정", "운전자금"],
            "debt_ratio_limit": 200.0,
            "interest_rate": "변동금리(현재 3.2%)",
            "apply_start": "2026-01-01",
            "apply_end": "2026-12-31",
            "is_amended": False
        },
        {
            # 케이스 4: 데이터가 매우 부실한 경우 (Default 값 테스트용)
            "program_id": "0000_1",
            "program_name": "기타 일반 지원사업",
            "category": "기타"
            # 필수 필드 대거 누락 시나리오
        }
    ]

    # 데이터프레임 변환
    df_test = pd.DataFrame(dummy_data)

    # DB 초기화 및 저장
    store = PolicyVectorStore(reset=True)
    store.add_policies(df_test)
    
    # 사용자 프로필
    test_user_profile = {
        "company_id": "TEST-001",
        "company_type": "중소기업",
        "industry_code": "58212",
        "industry_section": "J",
        "debt_ratio": 600.0,
        "business_age": 2,
        "region": "서울",
        "revenue": 500000000, # 5억
        "export_usd": 0,
        "employees": 10,
    }

    # 에이전트가 던질 예상 질문 (예: 사용자의 특허 정보)
    test_query = "인공지능 소프트웨어를 개발하는 기업을 위한 자금"

    # 필터링 작동 확인
    result = store.search_for_agent(test_user_profile, test_query, top_k=10)

    logger.info("=" * 30)
    logger.info("최종 검색 및 필터링 결과 리포트")
    logger.info("=" * 30)

    if result['green']:
        logger.info(f"🟢 [적격 사업] - {len(result['green'])}건")
        for p in result['green']:
            logger.info(f"  - [{p['score']:.3f}] {p['doc'].metadata.get('announcement_title', '제목없음')}")
    else:
        logger.info("🟢 [적격 사업] - 즉시 지원 가능한 사업이 없습니다.")

    if result['yellow']:
        logger.info(f"🟡 [조건부 반려] - {len(result['yellow'])}건")
        for y in result['yellow']:
            logger.info(f"  - [{y['score']:.3f}] {y['doc'].metadata.get('announcement_title', '제목없음')}")
            for reason in y['reasons']:
                logger.info(f"    └ 사유: {reason}")

    if result['red']:
        logger.info(f"🔴 [즉시 탈락] - {len(result['red'])}건")
        for r in result['red']:
            logger.info(f"  - [{r['score']:.3f}] {r['doc'].metadata.get('announcement_title', '제목없음')}")
            for reason in r['reasons']:
                logger.info(f"    └ 사유: {reason}")
    
    logger.info("=" * 30)