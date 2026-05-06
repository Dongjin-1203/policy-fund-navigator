import logging
import pandas as pd
import torch
import os
import pdfplumber
import olefile
import zlib
import struct
import glob
import json
import re
import boto3
from sentence_transformers import SentenceTransformer, util
from datetime import datetime
from dotenv import load_dotenv
from typing import Iterator, Dict, TypedDict, Any, List, Optional

from langchain_core.document_loaders import BaseLoader
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from langchain_google_genai import ChatGoogleGenerativeAI

from langgraph.graph import StateGraph, END

from templates import SUCCESS_WRAPPER, PROGRAM_ITEM_FORMAT, get_feedback_message

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ======================================================================
# KSIC 매퍼 모듈: 텍스트를 업종 코드로 변환
# ======================================================================
class IndustryMapper:
    def __init__(self, csv_path='data/ksic_master.csv'):
        logging.info("IndustryMapper 초기화 중... (KSIC 임베딩 로드)")
        try:
            self.df = pd.read_csv(csv_path)
            # 검색 품질을 높이기 위해 세세분류명과 중분류/대분류명을 결합하여 임베딩할 텍스트 생성
            self.df['search_text'] = self.df['section_name'] + " " + self.df['subclass_name']
            self.all_names = self.df['search_text'].tolist()
            
            # SBERT 모델 사용 (embedder.py와 동일한 모델)
            self.model = SentenceTransformer('snunlp/KR-SBERT-V40K-klueNLI-augSTS')
            self.name_embeddings = self.model.encode(self.all_names, convert_to_tensor=True)
            self.is_ready = True
        except Exception as e:
            logging.error(f"IndustryMapper 초기화 실패: {e}")
            self.is_ready = False

    def get_best_match(self, query_text: str, threshold: float = 0.5) -> Optional[Dict]:
        """업종명을 입력받아 가장 유사한 KSIC 코드(대분류, 세세분류)를 반환"""
        if not self.is_ready or not query_text:
            return None

        # 쿼리가 "업종 제한 없음" 등일 경우 매핑 안 함
        if "제한 없음" in query_text or "해당 없음" in query_text:
            return None

        query_embedding = self.model.encode(query_text, convert_to_tensor=True)
        cos_scores = util.cos_sim(query_embedding, self.name_embeddings)[0]
        
        top_results = torch.topk(cos_scores, k=1)
        score, idx = top_results.values[0].item(), top_results.indices[0].item()

        if score < threshold:
            logging.debug(f"매핑 실패 (점수 미달): '{query_text}' -> 최고 점수 {score:.2f}")
            return None

        matched_row = self.df.iloc[idx]
        return {
            "ind_section": str(matched_row['section_code']),
            "ind_code": str(matched_row['subclass_code']), # 5자리 코드
            "matched_name": str(matched_row['subclass_name']),
            "score": float(score)
        }

# 전역 Mapper 인스턴스 (매번 로드 방지)
mapper = IndustryMapper()

# ======================================================================
# Region 매퍼 모듈: 텍스트를 행정구역 코드로 변환
# ======================================================================
class RegionMapper:
    def __init__(self, csv_path='data/region_master.csv'):
        logging.info("RegionMapper 초기화 중... (Region 임베딩 로드)")
        try:
            self.df = pd.read_csv(csv_path)
            # region_name(예: "서울특별시 중구")을 임베딩 텍스트로 사용
            self.all_names = self.df['region_name'].tolist()
            
            # SBERT 모델 로드
            self.model = SentenceTransformer('snunlp/KR-SBERT-V40K-klueNLI-augSTS')
            self.name_embeddings = self.model.encode(self.all_names, convert_to_tensor=True)
            self.is_ready = True
        except Exception as e:
            logging.error(f"RegionMapper 초기화 실패: {e}")
            self.is_ready = False

    def get_best_match(self, query_text: str, threshold: float = 0.8) -> Optional[Dict]:
        """지역명을 입력받아 가장 유사한 행정구역 코드(region_code) 반환"""
        if not self.is_ready or not query_text:
            return None

        # 예외 처리: 전국구 사업인 경우
        if query_text in ["전국", "제한 없음", "해당 없음", "무관"]:
            return {"region_code": "NATIONWIDE", "region_name": "전국", "score": 1.0}

        query_embedding = self.model.encode(query_text, convert_to_tensor=True)
        cos_scores = util.cos_sim(query_embedding, self.name_embeddings)[0]
        
        top_results = torch.topk(cos_scores, k=1)
        score, idx = top_results.values[0].item(), top_results.indices[0].item()

        # 점수가 Threshold(0.8) 미만이면 행정구역이 아닌 '특수 지역(산업단지 등)'으로 간주
        if score < threshold:
            logging.debug(f"지역 매핑 실패 (특수 지역 간주): '{query_text}' -> 최고 점수 {score:.2f}")
            return None

        matched_row = self.df.iloc[idx]
        return {
            "region_code": str(matched_row['region_code']),
            "region_name": str(matched_row['region_name']),
            "score": float(score)
        }

# 전역 Mapper 인스턴스
region_mapper = RegionMapper()


# ======================================================================
# Extractor 모듈: 파일에서 텍스트만 추출
# ======================================================================
class GovernmentNoticeLoader(BaseLoader):
    def __init__(self, file_path: str):
        self.file_path = file_path

    def lazy_load(self) -> Iterator[Document]:
        file_ext = os.path.splitext(self.file_path)[1].lower()

        if file_ext == '.pdf':
            content = self._extract_pdf_with_coords()
        elif file_ext == '.hwp':
            content = self._extract_hwp_with_labels()
        else:
            content = "지원하지 않는 파일 형식입니다."

        # 예외 처리
        if len(content.strip()) < 50:
            logging.warning(f"경고: {self.file_path}에서 추출된 텍스트가 너무 짧습니다.")
            content = "추출 실패 또는 내용 없음"

        yield Document(
            page_content=content,
            metadata={"source": self.file_path, "type": file_ext}
        )

    def _extract_pdf_with_coords(self):
        """PDF에서 텍스트 추출.
        좌표 기반 표 영역 감지로 텍스트 중복을 방지하고 표는 마크다운으로 별도 추출.
        """
        all_text = ""
        with pdfplumber.open(self.file_path) as pdf:
            for page in pdf.pages:
                tables = page.find_tables()
                table_bboxes = [t.bbox for t in tables]

                def not_within_table(obj):
                    if obj.get("object_type") == "char":
                        for bbox in table_bboxes:
                            x0, top, x1, bottom = bbox
                            if (x0 <= obj["x0"] <= x1) and (top <= obj["top"] <= bottom):
                                return False
                    return True

                non_table_text = page.filter(not_within_table).extract_text() or ""

                extracted_tables = page.extract_tables()
                markdown_tables = ""
                for table in extracted_tables:
                    if not table:
                        continue
                    for row in table:
                        clean_row = [str(cell).replace('\n', ' ') if cell else "" for cell in row]
                        markdown_tables += "| " + " | ".join(clean_row) + " |\n"
                    markdown_tables += "\n"

                all_text += f"--- Page {page.page_number} ---\n"
                all_text += f"{non_table_text}\n"
                if markdown_tables:
                    all_text += f"\n[Table Data]\n{markdown_tables}\n"
        return all_text

    def _extract_hwp_with_labels(self):
        """HWP에서 텍스트 추출.
        BodyText 영역을 찾아 zlib 압축 해제 후 레코드 타입 기반 파싱.
        """
        f = olefile.OleFileIO(self.file_path)
        dirs = f.listdir()

        bodytext_dirs = [d for d in dirs if 'BodyText' in d]
        text_content = ""

        for section in bodytext_dirs:
            section_data = f.openstream(section).read()
            decompressed_data = zlib.decompress(section_data, -15)

            i = 0
            size = len(decompressed_data)
            while i < size:
                header = struct.unpack('<I', decompressed_data[i:i+4])[0]
                rec_type = header & 0x3ff
                rec_len = (header >> 20) & 0xfff

                if rec_type == 67:
                    data = decompressed_data[i+4:i+4+rec_len]
                    text_content += data.decode('utf-16', errors='ignore') + " "
                elif rec_type == 11:
                    text_content += "\n\n[Table Start]\n"
                elif rec_type == 66:
                    text_content += "\n[Row/Cell Boundary]\n"
                elif rec_type == 68:
                    text_content += "\n[Section Boundary]\n"

                i += 4 + rec_len
        return text_content

# ======================================================================
# Parser 모듈: LLM 체인 및 데이터 정제
# ======================================================================
def create_analysis_chain():
    api_key = os.environ.get("GEMINI_API_KEY")

    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        google_api_key=api_key,
        temperature=0,
    )

    system_prompt = """당신은 대한민국 정부지원사업 분석 전문가입니다.
    제시된 텍스트는 PDF에서 추출된 마크다운 표 구조와 HWP에서 추출된 지시어 포함 텍스트가 섞여 있습니다.
    제시된 지원사업 공고에서 추출된 텍스트를 분석하여, 기업 데이터베이스와 매칭 가능한 정량적/정성적 정보를 추출하세요.
    만약 한 공고문에 여러 개의 지원 세부 사업(트랙)이 포함되어 있다면 각각 분리하여 리스트로 만드세요.

    [표 데이터 해석 가이드]
    1. PDF 마크다운 표 (| --- |)
        - 행과 열의 관계를 엄격히 준수하세요.
    2. HWP 지시어 기반 텍스트 ([Table Start], [Row/Cell Boundary])
        - 지시어 사이의 단어들을 하나의 행(Row)으로 간주하여 논리적으로 재구성하세요.
        - 줄바꿈이 불규칙하더라도 업종-기간-금액-요건 등 순서가 보인다면 이를 하나의 사업 정보로 결합하세요.
    3. 수치 데이터(금액, 인원, 기간 등) 추출 시 앞뒤 단어의 문맥을 대조하여 정확한 의미를 판별하세요.
    4. 불확실한 정보는 추측하지 말고 '정보 없음'으로 표기하세요.

    [핵심 추출 항목 및 규칙]
    1. 'is_amended': 공고 내용 중 '변경', '정정', '재공고' 등의 내용이 있으면 True, 아니면 False로 표기하세요.
    2. 'announcement_title': 공고문 상단의 전체 제목을 추출하세요.
    3. 'programs' (1:N 대응): 공고 내에 세부 사업(트랙, 분야)이 나뉘어 있다면 각각 개별 객체로 추출하세요.
        사업이 하나뿐이라도 반드시 리스트(ARRAY) 내부에 1개의 객체로 포함시켜야 합니다.
    4. 'program_name': 각 세부 사업의 명칭을 기재하세요. 별도 세부명이 없다면 'announcement_title'과 동일하게 표기하세요.
    5. 'category': 공고에서 지원사업의 주요 카테고리를 나타냅니다.
        아래 리스트 중 공고의 주요 내용과 가장 잘 맞는 하나를 선택하세요.
        리스트: {categories}
    6. 'target_company_types': 지원 가능한 기업의 형태를 리스트로 추출하세요. (예: "중소기업", "창업기업", "소상공인", "중견기업" 등)
        정보가 없으면 빈 리스트로 나타내세요.
    7. 'target_industry_text': 지원 대상이 되는 모든 업종 한글 명칭을 리스트로 추출하세요. 
       (예: '제조업', '정보통신업', '건설업', '도매 및 소매업', '전문, 과학 및 기술 서비스업', '소프트웨어 개발 및 공급업', '탄소섬유 제조업', '광학 렌즈 및 광학 요소 제조업' 등)
       정보가 없으면 빈 리스트로 나타내세요.
    8. 'target_industry_codes': 공고에 지원 대상 업종이 KSIC 코드인 '대문자 알파벳 하나' 또는 '2~5자리 숫자'로 명시되어 있다면 리스트로 추출하세요.
        (예: "C", "J", "582" 등)
        정보가 없으면 빈 리스트로 나타내세요.
    9. 'excluded_industry_text': 공고에서 명시한 코드로 명확히 자를 수 있는 순수 지원 제외(제한) 업종 한글 명칭을 리스트로 추출하세요.
        (예: '주류 도매업', '담배 도매업', '주점업', '사행시설 관리 및 운영업' 등)
        정보가 없으면 빈 리스트로 나타내세요.
    10. 'excluded_industry_codes': 공고에 지원 제외 대상 업종이 KSIC 코드인 '대문자 알파벳 하나' 또는 '2~5자리 숫자'로 명시되어 있다면 리스트로 추출하세요.
        정보가 없으면 빈 리스트로 나타내세요.
    11. 'excluded_subset_condition': "업종 中 ~한 경우" 등 특정 업종 내에서 일부만 제외(제한)하는 조건부 업종 규정을
        객체 리스트 형태로 작성하세요: {{"code": "업종코드 또는 null", "condition": "제외 조건 텍스트"}}
        - 공고문이나 표에 '코드'가 명시되어 있다면 반드시 'code' 필드에 추출하세요. ('中' 등의 글자는 제거하고 숫자/알파벳만 추출)
        (예: "지원 제외 업종: 33402 中 불건전 영상게임기 제조업" -> {{"code": "33402", "condition": "불건전 영상게임기 제조업"}},
        "지원 제외 업종: 도매 및 소매업 中 담배 중개업" -> {{"code": null, "condition": "도매 및 소매업 중 담배 중개업"}})
        정보가 없으면 빈 리스트로 나타내세요.
    12. 'caution_notes': 휴폐업, 국세 체납, 부도, 사행성 등 업종 코드와 무관한 추상적인 결격 사유나 주의사항을 텍스트 리스트로 추출하세요.
        정보가 없으면 빈 리스트로 나타내세요.
    13. 'support_description': 최대 지원금액 외에 "수출액에 따른 차등 지원" 등 복잡한 지원금/지원항목 관련 설명을 텍스트로 요약하여 리스트로 추출하세요.
        정보가 없으면 빈 리스트로 나타내세요.
    14. 'region_raw': 공고에서 언급된 지원 대상 지역, 특정 산업단지, 경제자유구역, 특정 캠퍼스 명칭 등을 '표준화하지 말고 텍스트 원문 그대로' 리스트로 추출하세요.
        (예: "서울특별시", "반월·시화 국가산업단지", "G밸리", "제주 첨단과학기술단지" 등)
        특별한 언급이 없으면 ["전국"]으로 표기하세요.
    15. 'max_support': 공고에서 명시한 최대 지원 금액을 숫자로만 추출하세요 (단위: 원).
        (예: '최대 5,000만원'이면 50000000으로 표기).
        상한선이 없거나 파악 불가능하면 반드시 null로 표기하세요.
    16. 'min_export_usd' & 'max_export_usd': 지원 요건 중 '수출액' 기준이 불/달러($) 단위로 명시된 경우 숫자로 추출하세요. 
        조건이 없으면 반드시 null로 표기하세요.
    17. 'min_business_age' & 'max_business_age': 공고에서 요구하는 업력 제한을 찾아 숫자로 추출하세요. (단위: 년)
        - 중요: '미만'이라는 단어가 쓰였다면 해당 숫자에서 1을 빼서 '이하' 기준으로, '초과'라는 단어가 쓰였다면 해당 숫자에서 1을 더해서 '이상' 기준으로 출력하세요.
        (예: '업력 3년 이상' -> min_business_age=3, max_business_age=null, '업력 7년 미만' -> min_business_age=0, max_business_age=6)
        조건이 복합적일 경우 (예: "일반 7년, 신산업 10년"), 가장 넓은 범위(예: 10)를 숫자로 적고, 세부 내용은 'requirements'에 적어주세요.
        조건이 없으면 반드시 null로 표기하세요.
    18. 'min_revenue' & 'max_revenue': 공고에서 요구하는 매출액 제한을 찾아 아래 숫자로 추출하세요. (단위: 원)
        (예: '매출액 50억 이하' -> min_revenue=0, max_revenue=5000000000)
        조건이 없으면 반드시 null로 표기하세요.
    19. 'min_employees' & 'max_employees': 공고에서 요구하는 고용인원/근로자 수 제한을 찾아 아래 숫자로 추출하세요.
        - 중요: '미만'이라는 단어가 쓰였다면 해당 숫자에서 1을 빼서 '이하' 기준으로, '초과'라는 단어가 쓰였다면 해당 숫자에서 1을 더해서 '이상' 기준으로 출력하세요.
        (예: '상시 근로자 5인 이상' -> min_employee=5, max_employee=null, '근로자 수 3인 미만' -> min_employees=0, max_employees=2)
        조건이 없으면 반드시 null로 표기하세요.
    20. 'requirements': 공고 내용 중 기타 지원 자격 요건을 추출하여 리스트로 만드세요.
        정보가 없으면 빈 리스트로 나타내세요.
    21. 'technical_terms': 공고에서 언급된 기술 용어를 추출하되, 기업의 특허(KIPRIS) 데이터와 매칭에 유용한 기술 키워드를 추출하세요.
        AI, IT, ICT, SW, SaaS, Cloud, IoT, ML, DL 등 전문 기술 용어와 고유명사는 표준화된 용어로 통일하여 리스트로 만드세요.
        정보가 없으면 빈 리스트로 나타내세요.
    22. 'apply_start' & 'apply_end': 접수 시작 날짜와 접수 종료 날짜/마감일을 나타냅니다. YYYY-MM-DD 형식으로 추출하세요.
        '예산 소진 시까지', '상시 접수' 등의 경우에 마감일(apply_end)을 '9999-12-31'로 표기하세요.
        명시되지 않은 경우 null로 표기하세요.
    23. 'debt_ratio_limit': 공고에서 지원 대상 기업의 부채비율 상한을 숫자(%)로만 추출하세요.
        (예: '부채비율 500% 이하'이면 500으로 표기). 
        명시되지 않은 경우 null로 표기하세요.
    24. 'interest_rate': 공고에서 명시한 대출 금리를 문자열로 추출하세요.
        (예: '연 3.0%', '변동금리 + 0.5%p' 등). 
        명시되지 않은 경우 null로 표기하세요.
    """

    user_prompt = """아래 공고 내용을 분석하여 JSON 형식으로 반환하세요.
    공고 내용: {context}

    반드시 아래 JSON 구조를 지켜야 합니다:
    {{
    "is_amended": boolean,
    "announcement_title": "string",
    "programs": [
        {{
        "program_name": "string",
        "category": "string",
        "target_company_types": ["string"],
        "target_industry_text": ["string"],
        "target_industry_codes": ["string"],
        "excluded_industry_text": ["string"],
        "excluded_industry_codes": ["string"],
        "excluded_subset_condition": [
            {{ "code": "string", "condition": "string" }}
        ],
        "caution_notes": ["string"],
        "region_raw": ["string"],
        "support_description": ["string"],
        "max_support": integer or null,
        "min_export_usd": integer or null,
        "max_export_usd": integer or null,
        "min_business_age": integer or null,
        "max_business_age": integer or null,
        "min_revenue": integer or null,
        "max_revenue": integer or null,
        "min_employees": integer or null,
        "max_employees": integer or null,
        "requirements": ["string"],
        "technical_terms": ["string"],
        "debt_ratio_limit": number or null,
        "interest_rate": "string" or null,
        "apply_start": "YYYY-MM-DD",
        "apply_end": "YYYY-MM-DD"
        }}
        ]
    }}
    """

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("user", user_prompt)
    ])

    parser = JsonOutputParser()

    chain = prompt | llm | parser
    return chain

# 트리 모델용 피처 추출기
def extract_numerical_features(parsed_data: Dict) -> Dict:
    """트리 모델(LightGBM) 스코어링을 위한 정량적 피처 추출"""
    features = {}

    # 결측치 기본값
    MAX_REVENUE = 10**15
    MAX_AGE = 999
    MAX_EMPLOYEES = 10**7 
    MAX_DEBT_RATIO = 999999.0
    MAX_EXPORT_USD = 10**12 # 1조 달러
    
    # 최대 지원 금액
    features["max_support_amount"] = parsed_data.get("max_support", 0)
    # 부채 비율
    debt_ratio = parsed_data.get("debt_ratio_limit")
    features["debt_ratio_limit"] = float(debt_ratio) if debt_ratio is not None else MAX_DEBT_RATIO
    # 업력
    features["min_business_age"] = parsed_data.get("min_business_age") or 0
    max_age = parsed_data.get("max_business_age")
    features["max_business_age"] = max_age if max_age is not None else MAX_AGE
    # 매출액
    features["min_revenue"] = parsed_data.get("min_revenue") or 0
    max_rev = parsed_data.get("max_revenue")
    features["max_revenue"] = max_rev if max_rev is not None else MAX_REVENUE
    # 수출액(USD)
    features["min_export_usd"] = parsed_data.get("min_export_usd") or 0
    max_exp = parsed_data.get("max_export_usd")
    features["max_export_usd"] =  max_exp if max_exp is not None else MAX_EXPORT_USD
    # 고용인원
    features["min_employees"] = parsed_data.get("min_employees") or 0
    max_emp = parsed_data.get("max_employees")
    features["max_employees"] = max_emp if max_emp is not None else MAX_EMPLOYEES

    return features

# ================================================================================
# Graph 모듈: API 데이터와 파일 데이터를 모두 수용
# ================================================================================
# LangGraph 에이전트 구조
class GraphState(TypedDict):
    file_path: Optional[str]
    raw_content: Optional[str]
    parsed_json: Dict[str, Any]
    numerical_features: Dict[str, float]
    is_valid: bool
    retry_count: int
    rejection_reasons: List[str]  # 탈락 사유 코드들
    final_feedback: str  # 최종 완성된 텍스트

# Node 함수들 정의
def extract_node(state: GraphState):
    """파일이 주어지면 텍스트와 표를 추출하고 이미 텍스트가 있으면 패스"""
    # API나 크롤링에서 raw_content를 직접 넘겨준 경우 추출 생략
    if state.get("raw_content") and not state.get("file_path"):
        logging.info("--- [Node 1] 텍스트 다이렉트 입력 감지 (파일 추출 생략) ---")
        return {}

    logging.info(f"--- [Node 1] 텍스트 추출 중: {os.path.basename(state['file_path'])} ---")
    loader = GovernmentNoticeLoader(state['file_path'])
    docs = list(loader.lazy_load())
    return {"raw_content": docs[0].page_content}

def parse_node(state: GraphState):
    """LLM을 이용해 정형 JSON으로 변환"""
    logging.info(f"--- [Node 2] LLM 파싱 중 (시도 횟수: {state.get('retry_count', 0) + 1})---")
    chain = create_analysis_chain()
    categories = {"금융", "기술", "인력", "수출", "내수", "창업", "경영", "기타"}

    try:
        result = chain. invoke({"context": state['raw_content'], "categories": categories})
        return {"parsed_json": result, "retry_count": state.get("retry_count", 0) + 1}
    except Exception as e:
        logging.error(f"파싱 실패: {e}")
        return {"is_valid": False, "retry_count": state.get("retry_count", 0) + 1}
    
def validate_node(state: GraphState):
    """데이터 정합성 검증 및 피처 병합"""
    logging.info("--- [Node 3] 데이터 정합성 검증 및 하이브리드 업종 매핑 ---")
    data = state.get('parsed_json', {})

    # 필수 필드 검사
    if not data or "programs" not in data:
        return {"is_valid": False}
    programs = data.get("programs", [])
    if not programs: 
        return {"is_valid": False}

    # 정량 지표
    numeric_fields = [
        'max_support', 'min_business_age', 'max_business_age', 
        'min_revenue', 'max_revenue', 'min_employees', 'max_employees', 'debt_ratio_limit'
    ]

    for prog in programs:
        # 정량 지표 부재 체크
        # 하나라도 숫자가 있으면(None이 아니면) 통과
        has_any_metric = any(prog.get(field) is not None for field in numeric_fields)
        if not has_any_metric:
            logging.warning(f"검증 실패: '{prog.get('program_name')}'에 정량 지표가 전혀 없습니다.")
            return {"is_valid": False}

        # 날짜 논리 오류
        if prog.get("apply_start") and prog.get("apply_end"):
            if prog["apply_start"] > prog["apply_end"]:
                logging.warning("날짜 논리 오류: 마감일이 시작보다 빠릅니다. LLM에게 재파싱을 요청합니다.")
                return {"is_valid": False}
        
        # 지원 대상 업종 매핑
        raw_target_codes = prog.get('target_industry_codes', [])
        target_codes, target_sections = [], []
        for code in raw_target_codes:
            # 정규식 수정: 알파벳과 숫자 모두 허용
            clean_code = re.sub(r'[^a-zA-Z0-9]', '', str(code)).upper()
            if clean_code.isalpha(): # 알파벳이면 섹션(대분류)
                target_sections.append(clean_code)
            elif clean_code.isdigit(): # 숫자면 코드(중~세세분류)
                target_codes.append(clean_code)
        # 중복 제거 및 JSON 삽입
        prog['target_ksic_codes'] = list(set(target_codes))
        prog['target_ksic_sections'] = list(set(target_sections))
        
        # 제외 대상 순수 업종 매핑
        raw_exclude_codes = prog.get('excluded_industry_codes', [])
        exclude_codes, exclude_sections = [], []
        for e_code in raw_exclude_codes:
            e_clean = re.sub(r'[^a-zA-Z0-9]', '', str(e_code)).upper()
            if e_clean.isalpha(): 
                exclude_sections.append(e_clean)
            elif e_clean.isdigit(): 
                exclude_codes.append(e_clean)

        # 예외 처리용 무시 키워드 리스트
        ignore_keywords = ["해당 없음", "해당없음", "제한 없음", "제한없음", "N/A", "-"]

        # 텍스트 매핑 결과 합치기
        for text in prog.get('excluded_industry_text', []):
            # 1차 필터: 무시 키워드가 포함되어 있다면 매퍼를 돌리지 않고 건너뜀
            if any(keyword in text for keyword in ignore_keywords):
                logging.info(f"노이즈 텍스트 무시됨: '{text}'")
                continue

            match = mapper.get_best_match(text)
            if match: 
                exclude_codes.append(match['ind_code'])
                exclude_sections.append(match['ind_section'])

        prog['excluded_ksic_codes'] = list(set(exclude_codes))
        prog['excluded_ksic_sections'] = list(set(exclude_sections))

        # 조건부 제외 업종 - 한자 中 처리
        conditional_infos = []
        for cond_obj in prog.get('excluded_subset_condition', []):
            extracted_code = cond_obj.get('code')
            condition_text = cond_obj.get('condition', '')
            
            if extracted_code:
                # LLM이 "33402 中" 처럼 불필요한 문자를 가져왔을 경우를 대비해 숫자/알파벳만 추출
                clean_code = re.sub(r'[^a-zA-Z0-9]', '', str(extracted_code))
                if clean_code:
                    conditional_infos.append({
                        "code": clean_code, 
                        "condition": condition_text
                    })
            else:
                # 코드가 없고 텍스트만 있는 경우 SBERT 매퍼 사용
                match = mapper.get_best_match(condition_text)
                if match:
                    conditional_infos.append({
                        "code": match['ind_code'],
                        "condition": condition_text
                    })
        prog['conditional_excluded_ksic_infos'] = conditional_infos

        # 지역 시맨틱 매핑 및 특수 구역(산업단지 등) 분리
        mapped_regions = []  # 행정구역 코드 저장
        special_zones = []   # 행정구역이 아닌 특수 명칭(산업단지 등) 보존

        for raw_text in prog.get('region_raw', []):
            match = region_mapper.get_best_match(raw_text, threshold=0.8)
            if match:
                mapped_regions.append(match['region_code'])
            else:
                special_zones.append(raw_text)

        prog['mapped_region_codes'] = list(set(mapped_regions))
        prog['special_zones'] = list(set(special_zones))


    # 트리 모델용 피처 추출 및 병합
    if data.get("programs"):
        features = extract_numerical_features(data["programs"][0])
        return {"is_valid": True, "numerical_features": features, "parsed_json": data}
    return {"is_valid": False}

# 피드백 생성 노드
def feedback_node(state: GraphState):
    logging.info("--- [Node 4] 피드백 생성 중 ---")

    # 파싱된 데이터 및 피처 가져오기
    parsed_data = state.get('parsed_json', {})
    programs = parsed_data.get("programs", [])
    if not programs:
        return {"final_feedback": "세부 사업 정보를 찾을 수 없습니다."}
    
    green_items = []

    for idx, prog in enumerate(programs):
        # 임시 데이터 (나중에 DB 연동 시 실제 값으로 교체)
        item_str = PROGRAM_ITEM_FORMAT.format(
            score=0.999,  # 파싱 단계이므로 임시 점수 부여
            program_name=prog.get("program_name", f"트랙 {idx+1}"),
            category=prog.get("category", "기타"),
            apply_end=prog.get("apply_end", "상시")
        )
        green_items.append(item_str)

    # 완성된 리스트를 SUCCESS_WRAPPER에 끼워 넣기
    matched_programs_str = "\n".join(green_items)
    final_msg = SUCCESS_WRAPPER.format(matched_programs=matched_programs_str)

    # 완성된 메시지를 합쳐서 State에 저장
    return {"final_feedback": final_msg}

# 조건부 로직(Edge) 정의
def should_continue(state: GraphState):
    if state["is_valid"]:
        return "feedback"
    elif state["retry_count"] >= 3:
        logging.error("최대 재시도 횟수 초과. 강제 종료합니다.")
        return "end"
    else:
        logging.info("데이터 이상감지. 파싱을 재시도합니다.")
        return "retry"
    
# 그래프 구성
def build_parser_graph():
    workflow = StateGraph(GraphState)
    workflow.add_node("extract", extract_node)
    workflow.add_node("parse", parse_node)
    workflow.add_node("validate", validate_node)
    workflow.add_node("feedback", feedback_node)

    workflow.set_entry_point("extract")
    workflow.add_edge("extract", "parse")
    workflow.add_edge("parse", "validate")
    # 검증 실패 시 다시 추출(혹은 변환)로 돌아가는 순환 구조
    workflow.add_conditional_edges(
        "validate",
        should_continue,
        {
            "feedback": "feedback",
            "end": END,
            "retry": "parse"
            }
            )
    # 피드백이 끝나면 최종 종료(END)
    workflow.add_edge("feedback", END)

    return workflow.compile()

# =============================================================
# 실행부
# =============================================================
# 데이터 평탄화 (1(공고):N(세부사업) 구조 -> 1(Row):1(사업) 구조)
def flatten_results(parsed_results: List[Dict]) -> List[Dict]:
    """
    Nested JSON(1:N)을 VDB용 Flat 데이터(1:1)로 변환
    """
    flat_data = []
    
    for entry in parsed_results:
        # 공통 정보 (Top-level)
        common_info = {
            "announcement_title": entry.get("announcement_title"),
            "is_amended": entry.get("is_amended"),
            "source_file": entry.get("source_file")
        }
        
        # 세부 사업별로 분리 (Nested programs)
        for idx, prog in enumerate(entry.get("programs", [])):
            # 파일명_순번 형태로 고유 ID 생성 (S3 파일명 겹침 방지)
            unique_id = f"{common_info['source_file']}_{idx}"
            flat_row = {**common_info, **prog, "program_id": unique_id}
            flat_data.append(flat_row)
            
    return flat_data

def upload_to_s3(nested_results: list, flat_results: list, today: str) -> None:
    s3_bucket = os.environ.get('S3_BUCKET_NAME')
    if not s3_bucket:
        logging.warning("S3_BUCKET_NAME 미설정 — S3 업로드 건너뜀")
        return

    try:
        s3_client = boto3.client(
            's3',
            aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
        )
        # 계층형 원본 저장 (Original Nested)
        for result in nested_results:
            source_id = result.get('source_file', 'unknown')
            s3_key = f"raw_parsing/{today}/{source_id}_nested.json"
            s3_client.put_object(
                Bucket=s3_bucket, 
                Key=s3_key, 
                Body=json.dumps(result, ensure_ascii=False, indent=4),
                ContentType='application/json',
            )

        # 평탄화된 세부 사업 저장 (Flat for VDB)
        for result in flat_results:
            program_id = result.get('program_id',result.get('source_file', 'unknown'))
            s3_key = f"embeddings/requirements_db/{today}/{program_id}.json"
            s3_client.put_object(
                Bucket=s3_bucket,
                Key=s3_key,
                Body=json.dumps(result, ensure_ascii=False, indent=4),
                ContentType='application/json',
            )
            logging.info(f"S3 업로드 완료: {s3_bucket} (원본 {len(nested_results)}건 / 평탄화 {len(flat_results)}건)")
    except Exception as e:
        logging.error("S3 업로드 실패: %s", e)


if __name__ == "__main__":
    # 파일 기반 처리
    folder_path = "raw_data"
    file_list = (
        glob.glob(os.path.join(folder_path, "*.pdf"))
        + glob.glob(os.path.join(folder_path, "*.hwp"))
    )

    logging.info("총 %d개의 파일을 LangGraph 에이전트로 분석합니다.", len(file_list))

    app = build_parser_graph()
    final_results = []

    for file_path in file_list:
        initial_state = {"file_path": file_path, "retry_count": 0, "is_valid": False}
        # 그래프 실행
        result_state = app.invoke(initial_state)

        if result_state.get("is_valid"):
            final_data = result_state["parsed_json"]
            base_name = os.path.basename(file_path)
            # 파일명 맨 앞의 숫자(slno)만 추출해서 저장 (크롤러가 {slno}_{파일명} 형태로 저장하므로)
            slno = base_name.split('_')[0] if '_' in base_name else base_name.rsplit('.', 1)[0]
            final_data['source_file'] = slno
            # 테스트용
            final_data['extracted_feature_for_model'] = result_state.get("numerical_features")
            final_results.append(final_data)
            logging.info(f"완료: {final_data['source_file']}")
        else:
            logging.error(f"최종 실패: {os.path.basename(file_path)}")

    # API 텍스트 실행 예시
    """
    api_text = "2026년 AI 고도화 사업. 매출액 50억 이하 지원..."
    api_state = {"file_path": None, "raw_content": api_text, "retry_count": 0, "is_valid": False}
    api_result = app.invoke(api_state)
    """

    # 수집된 계층형 데이터를 평탄화
    flattened_results = flatten_results(final_results)

    # 결과 저장
    with open("analysis_results.json", "w", encoding="utf-8") as f:
        json.dump(flattened_results, f, ensure_ascii=False, indent=4)

    today = datetime.now().strftime('%Y-%m-%d')
    upload_to_s3(final_results, flattened_results, today)

    logging.info("에이전트 분석 완료. 결과가 analysis_results.json 및 S3에 저장되었습니다.")