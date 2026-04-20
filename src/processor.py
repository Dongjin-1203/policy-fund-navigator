import pdfplumber
import os
import json
from dotenv import load_dotenv
from google import genai
from google.genai.errors import ClientError

load_dotenv()

class PDFProcessor:
    def __init__(self, folder_path):
        self.folder_path = folder_path

        api_key = os.getenv("GEMINI_API_KEY")
        self.client = genai.Client(api_key=api_key)
        self.model_id = "gemini-2.5-flash"

    def extract_all_from_folder(self):
        """
        폴더 내 모든 PDF 파일을 열어서 텍스트 추출
        """
        all_texts = {}

        # 폴더 내 파일 목록을 하나씩 확인
        for filename in os.listdir(self.folder_path):
            # 파일 확장자가 .pdf인 경우만 처리
            if filename.endswith('.pdf'):
                file_path = os.path.join(self.folder_path, filename)
                print(f"--- [{filename}] 읽기 시작 ---")

                text = self._extract_text(file_path)
                all_texts[filename] = text
        return all_texts
    
    def _extract_text(self, pdf_path):
        """ PDF에서 텍스트 추출하는 내부 함수 """
        all_text = ""
        # pdfplumber로 PDF 열기
        with pdfplumber.open(pdf_path) as pdf:
            # 각 페이지에서 텍스트 추출
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    all_text += page_text + "\n"
        return all_text

    def parse_with_llm(self, raw_text):
        """
        LLM을 활용하여 추출된 텍스트에서 필요한 정보만 파싱하여 JSON으로 바꿔주는 함수
        Gemini 공식 문서의 'JSON 응답 제어' 섹션을 참고하여
        AI가 JSON만 반환하도록 강제
        """
        # 프롬프트
        prompt = f"""
        당신은 정부지원사업 분석 전문가입니다.
        아래 지원사업 공고문에서 추출된 텍스트를 분석하여 정보를 추출하세요.

        [지시 사항]
        1. 모든 설명과 일반 명사는 한국어로 작성하세요
        2. 하지만 AI, IT, ICT, SW, SaaS, Cloud, IoT, ML, DL 등 전문 기술 용어와 고유명사는 표준화된 용어를 우선 통일하세요. 한국어와 영어가 혼용되는 경우 영문 약어를 기본으로 하되 괄호 안에 한국어를 병기하지 마세요.
        3. 지역명은 서울, 경기, 인천, 부산, 대구, 광주, 대전, 울산, 세종, 강원, 충북, 충남, 전북, 전남, 경북, 경남, 제주 등 한국어 표준 명칭으로 통일하세요. (예: 서울특별시 → 서울)
        
        공고문 내용:
        {raw_text[:3000]}
        """

        print(f"--- 제미나이가 공고를 정밀 분석 중입니다...---")
        try:
            response = self.client.models.generate_content(
                model=self.model_id,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": {
                        'type': 'OBJECT',
                        'properties': {
                            'target_sector': {'type': 'STRING'},
                            'target_location': {'type': 'STRING'},
                            'required_tech': {
                                'type': 'ARRAY',
                                'items': {'type': 'STRING'}
                            }
                        }
                    }
                }
            )
        except ClientError as e:
            error_message = getattr(e, 'error_json', None) or str(e)
            print("AI 호출 중 오류가 발생했습니다:")
            print(error_message)
            print("잠시 후 다시 시도하거나 API 쿼터를 확인하세요. 이 파일은 기본값으로 처리합니다.")
            return {"target_sector": "미분류", "target_location": "전국", "required_tech": []}

        # AI의 대답에서 JSON 부분만 추출 (실제 모델 응답에 따라 조정 필요)
        try:
            return json.loads(response.text)
        except Exception as e:
            print(f"AI 분석 오류 발생: {e}")
            return {"target_sector": "미분류", "target_location": "전국", "required_tech": []}
    
    
# 테스트 실행
if __name__ == "__main__":
    processor = PDFProcessor("raw_data")
    results = processor.extract_all_from_folder()

    for name, text in results.items():
        print(f"\n" + "="*40)
        print(f"[파일명: {name}] 분석 시작")

        # AI에게 분석 시킴
        parsed_data = processor.parse_with_llm(text)

        print(f"--- AI 분석 결과 ---")
        print(json.dumps(parsed_data, indent=4, ensure_ascii=False))
        print("="*40)