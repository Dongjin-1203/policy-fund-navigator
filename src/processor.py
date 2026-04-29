import json
import logging
import os
import pdfplumber
from dotenv import load_dotenv
from google import genai
from google.genai.errors import ClientError

load_dotenv()

logger = logging.getLogger(__name__)


class PDFProcessor:
    def __init__(self, folder_path):
        self.folder_path = folder_path

        api_key = os.getenv("GEMINI_API_KEY")
        self.client = genai.Client(api_key=api_key)
        self.model_id = "gemini-2.5-flash"

    def extract_all_from_folder(self):
        """폴더 내 모든 PDF 파일을 열어서 텍스트 추출."""
        all_texts = {}

        for filename in os.listdir(self.folder_path):
            if filename.endswith('.pdf'):
                file_path = os.path.join(self.folder_path, filename)
                logger.info('[%s] 읽기 시작', filename)

                text = self._extract_text(file_path)
                all_texts[filename] = text
        return all_texts

    def _extract_text(self, pdf_path):
        """PDF에서 텍스트 추출하는 내부 함수."""
        all_text = ""
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    all_text += page_text + "\n"
        return all_text

    def parse_with_llm(self, raw_text):
        """공고문 텍스트를 Gemini API로 파싱하여 program_features 스키마 형식으로 반환.

        Returns:
            {
                "program_id":       str,  # 호출 측에서 주입
                "industry_limit":   str,  # 지원 제외 업종
                "debt_ratio_limit": str,  # 부채비율 상한
                "requirements":     str   # 기타 자격요건
            }
        """
        prompt = f"""
        당신은 정부지원사업 분석 전문가입니다.
        아래 지원사업 공고문에서 추출된 텍스트를 분석하여 자격요건 정보를 추출하세요.

        [지시 사항]
        1. 모든 설명과 일반 명사는 한국어로 작성하세요.
        2. AI, IT, ICT, SW, SaaS, Cloud, IoT, ML, DL 등 전문 기술 용어와 고유명사는 영문 약어로 통일하세요.
        3. 업종명은 한국표준산업분류(KSIC) 기준 명칭으로 작성하세요.
        4. industry_limit: 지원이 제외되는 업종을 콤마 구분 문자열로 작성하세요. 없으면 "없음"으로 작성하세요.
        5. debt_ratio_limit: 부채비율 상한을 숫자+% 형식으로 작성하세요 (예: "200%"). 명시되지 않으면 "제한없음"으로 작성하세요.
        6. requirements: 업종·부채비율 외 나머지 자격요건을 한 문장으로 요약하세요.

        공고문 내용:
        {raw_text[:3000]}
        """

        logger.info('Gemini API 호출 중 (model=%s)', self.model_id)
        try:
            response = self.client.models.generate_content(
                model=self.model_id,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": {
                        'type': 'OBJECT',
                        'properties': {
                            'industry_limit':   {'type': 'STRING'},
                            'debt_ratio_limit': {'type': 'STRING'},
                            'requirements':     {'type': 'STRING'},
                        },
                        'required': ['industry_limit', 'debt_ratio_limit', 'requirements'],
                    }
                }
            )
        except ClientError as e:
            error_message = getattr(e, 'error_json', None) or str(e)
            logger.error('Gemini API 호출 오류: %s', error_message)
            return {
                'industry_limit':   '미분류',
                'debt_ratio_limit': '제한없음',
                'requirements':     '',
            }

        try:
            return json.loads(response.text)
        except Exception as e:
            logger.error('응답 JSON 파싱 오류: %s', e)
            return {
                'industry_limit':   '미분류',
                'debt_ratio_limit': '제한없음',
                'requirements':     '',
            }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    processor = PDFProcessor("raw_data")
    results = processor.extract_all_from_folder()

    for name, text in results.items():
        logger.info('=' * 40)
        logger.info('[%s] 분석 시작', name)

        parsed_data = processor.parse_with_llm(text)

        logger.info('분석 결과: %s', json.dumps(parsed_data, indent=4, ensure_ascii=False))
        logger.info('=' * 40)
