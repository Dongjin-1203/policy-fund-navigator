import os
import pdfplumber
import olefile
import zlib
import struct
import glob
import json
import logging
import boto3
from datetime import datetime
from dotenv import load_dotenv
from typing import Iterator

from langchain_core.document_loaders import BaseLoader
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from langchain_google_genai import ChatGoogleGenerativeAI

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


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
    4. 'sub_title': 각 세부 사업의 명칭을 기재하세요. 별도 세부명이 없다면 'main_title'과 동일하게 표기하세요.
    5. 'program_category': 공고에서 지원사업의 주요 카테고리를 나타냅니다.
        아래 리스트 중 공고의 주요 내용과 가장 잘 맞는 하나를 선택하세요.
        리스트: {categories}
    6. 'industry_major_category': 공고에서 지원 대상이 되는 산업을 '한국표준산업분류(KSIC) 대분류' 명칭 기준으로 추출하되,
        대분류 명칭과 코드를 함께 명시하세요.
        (예: '제조업(C)', '정보통신업(J)', '건설업(F)', '도매 및 소매업(G)', '전문, 과학 및 기술 서비스업(M)' 등)
    7. 'eligible_industries': 공고에서 명시한 지원 가능 업종(예: '탄소섬유 제조업', '광학 렌즈 및 광학 요소 제조업' 등)을 추출하세요.
    8. 'industry_limit': 공고에서 명시한 지원 제외 업종을 추출하세요.
        제외 업종 정보를 리스트로 만드세요.
    9. 'region': 공고에서 지원 대상 지역을 행정구역 표준 명칭(광역지방자치단체기준)(예: '서울특별시'->'서울', '경기도'->'경기' 등)으로 통일하여 추출하세요.
        포괄적인 명칭이 나오면 해당되는 광역 지자체를 모두 나열하세요.
        지역명이 명확히 언급되지 않은 경우 '전국'으로 표기하세요.
        특정 시/군 단위나 산업단지 제한이 있는 경우 등 광역 지자체보다 더 세부적인 경우에는 '지역명(세부)' 형식으로 표기하세요 (예: '경기(화성)', '전국(산업단지)' 등).
        시/군/구 단위는 생략합니다.
    10. 'max_support': 공고에서 명시한 최대 지원 금액을 숫자로만 추출하세요 (단위: 원).
        (예: '최대 5,000만원'이면 50000000으로 표기).
        상한선이 없거나 파악 불가능하면 0으로 표기하세요.
    11. 'requirements': 공고에서 지원 자격 요건을 수치 위주로 리스트로 만들며, 기업 스펙과 대조할 수 있도록 다음 수치를 반드시 포함하세요.
        - 업력 제한 (예: '업력 7년 미만', '창업 3년 이상' 등)
        - 매출액/자본금 제한 (예: '매출액 50억 이하', '자본금 10억 이상' 등)
        - 고용인원/상시 근로자 수 제한 (예: '상시 근로자 5인 이상', '고용인원 100인 이하' 등)
        자격 요건이 명확히 언급되지 않은 경우 '정보 없음'으로 표기하세요.
    12. 'technical_terms': 공고에서 언급된 기술 용어를 추출하되, 기업의 특허(KIPRIS) 데이터와 매칭에 유용한 기술 키워드를 추출하세요.
        AI, IT, ICT, SW, SaaS, Cloud, IoT, ML, DL 등 전문 기술 용어와 고유명사는 표준화된 용어로 통일하여 리스트로 만드세요.
    13. 'apply_start' & 'apply_end': 접수 시작 날짜와 접수 종료 날짜/마감일을 나타냅니다. YYYY-MM-DD 형식으로 추출하세요.
        '예산 소진 시까지', '상시 접수' 등의 경우에 마감일(apply_end)을 '9999-12-31'로 표기하세요.
    14. 'debt_ratio_limit': 공고에서 지원 대상 기업의 부채비율 상한을 숫자(%)로만 추출하세요.
        (예: '부채비율 500% 이하'이면 500으로 표기). 명시되지 않은 경우 null로 표기하세요.
    15. 'interest_rate': 공고에서 명시한 대출 금리를 문자열로 추출하세요.
        (예: '연 3.0%', '변동금리 + 0.5%p' 등). 명시되지 않은 경우 null로 표기하세요.
    """

    user_prompt = """아래 공고 내용을 분석하여 JSON 형식으로 반환하세요.
    공고 내용: {context}

    반드시 아래 JSON 구조를 지켜야 합니다:
    {{
    "is_amended": boolean,
    "announcement_title": "string",
    "programs": [
        {{
        "sub_title": "string",
        "program_category": "string",
        "industry_major_category": ["string"],
        "eligible_industries": ["string"],
        "industry_limit": ["string"],
        "region": ["string"],
        "max_support": integer,
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


def upload_to_s3(results: list, today: str) -> None:
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
        for result in results:
            program_id = result.get('source_file', 'unknown').rsplit('.', 1)[0]
            s3_key = f"embeddings/requirements_db/{today}/{program_id}.json"
            s3_client.put_object(
                Bucket=s3_bucket,
                Key=s3_key,
                Body=json.dumps(result, ensure_ascii=False, indent=4),
                ContentType='application/json',
            )
            logging.info("S3 업로드 완료: s3://%s/%s", s3_bucket, s3_key)
    except Exception as e:
        logging.error("S3 업로드 실패: %s", e)


if __name__ == "__main__":
    folder_path = "raw_data"
    file_list = (
        glob.glob(os.path.join(folder_path, "*.pdf"))
        + glob.glob(os.path.join(folder_path, "*.hwp"))
    )

    logging.info("총 %d개의 파일을 분석합니다.", len(file_list))

    chain = create_analysis_chain()
    final_results = []

    for file_path in file_list:
        try:
            logging.info("처리 중: %s", os.path.basename(file_path))
            loader = GovernmentNoticeLoader(file_path)
            docs = loader.load()

            program_categories = {"금융", "기술", "인력", "수출", "내수", "창업", "경영", "기타"}
            result = chain.invoke({
                "context": docs[0].page_content,
                "categories": program_categories
            })

            # {slno}_{filename} 형식에서 slno 추출
            basename = os.path.basename(file_path)
            underscore_idx = basename.find('_')
            if underscore_idx > 0 and basename[:underscore_idx].isdigit():
                slno = basename[:underscore_idx]
            else:
                slno = os.path.splitext(basename)[0]
            result['source_file'] = slno
            final_results.append(result)
            logging.info("완료: slno=%s", slno)

        except Exception as e:
            logging.error("%s 처리 중 오류 발생: %s", file_path, e)

    with open("analysis_results.json", "w", encoding="utf-8") as f:
        json.dump(final_results, f, ensure_ascii=False, indent=4)

    today = datetime.now().strftime('%Y-%m-%d')
    upload_to_s3(final_results, today)

    logging.info("모든 파일 분석 완료. 결과가 analysis_results.json에 저장되었습니다.")
