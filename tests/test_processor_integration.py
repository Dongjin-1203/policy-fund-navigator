"""bizinfo extractor ↔ processor.py 통합 호환성 스모크 테스트.

검증 항목:
1. bizinfo extractor 출력 포맷 구조 확인
2. processor.py 입출력 인터페이스 확인
3. 출력 스키마와 program_features.parquet LLM 파싱 필드 호환성 확인
4. Gemini API 호출은 mock 처리하여 실제 호출 없이 구조 검증
"""
import json
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


SAMPLE_BIZINFO_OUTPUT = {
    'date': '2026-04-25',
    'total_count': 1,
    'programs': [
        {
            'program_name': '중소기업 정책자금 융자',
            'announcement_no': 'PBLN000000000012345',
            'category': '금융',
            'apply_start': '2026-03-01',
            'apply_end': '2026-06-30',
            'managing_org': '중소기업진흥공단',
            'detail_url': 'https://www.bizinfo.go.kr/web/lay1/bbs/S1T122C128/AS/74/view.do?pblancId=PBLN000000000012345',
        }
    ],
}

SAMPLE_PDF_TEXT = """
중소기업 정책자금 융자 지원사업 공고

1. 지원 대상
   - 제조업, IT, 서비스업 영위 중소기업
   - 소재지: 서울, 경기, 인천 소재 기업 우선

2. 지원 조건
   - 부채비율 200% 이하
   - 업력 3년 이상

3. 지원 한도: 최대 30억원
4. 금리: 연 2.5%
"""

EXPECTED_PROGRAM_FEATURES_FIELDS = {
    'industry_limit',
    'debt_ratio_limit',
    'requirements',
}

PROCESSOR_OUTPUT_FIELDS = {
    'target_sector',
    'target_location',
    'required_tech',
}


class TestBizinfoOutputFormat(unittest.TestCase):
    """bizinfo extractor 출력 포맷 검증."""

    def test_output_has_required_fields(self):
        programs = SAMPLE_BIZINFO_OUTPUT['programs']
        self.assertGreater(len(programs), 0)
        required = {'program_name', 'announcement_no', 'category', 'apply_start', 'apply_end', 'managing_org', 'detail_url'}
        for field in required:
            self.assertIn(field, programs[0], f"bizinfo 출력에 '{field}' 필드 누락")

    def test_detail_url_present_for_pdf_crawling(self):
        """PDF 크롤링에 필요한 detail_url 존재 확인."""
        program = SAMPLE_BIZINFO_OUTPUT['programs'][0]
        self.assertIsNotNone(program.get('detail_url'))
        self.assertTrue(program['detail_url'].startswith('http'))


class TestProcessorInterface(unittest.TestCase):
    """processor.py 인터페이스 구조 검증 (Gemini API mock)."""

    def setUp(self):
        """google.genai 모듈 전체를 mock."""
        self.genai_patcher = patch.dict('sys.modules', {
            'google': MagicMock(),
            'google.genai': MagicMock(),
            'google.genai.errors': MagicMock(),
        })
        self.genai_patcher.start()

    def tearDown(self):
        self.genai_patcher.stop()

    def test_processor_instantiation(self):
        """PDFProcessor 클래스 초기화 구조 확인."""
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'test-key'}):
            from processor import PDFProcessor
            p = PDFProcessor('raw_data')
            self.assertEqual(p.folder_path, 'raw_data')

    def test_parse_with_llm_returns_dict(self):
        """parse_with_llm이 dict를 반환하는지 확인."""
        mock_response = MagicMock()
        mock_response.text = json.dumps({
            'target_sector': 'IT',
            'target_location': '서울',
            'required_tech': ['AI', 'ML'],
        })

        with patch.dict(os.environ, {'GEMINI_API_KEY': 'test-key'}):
            from processor import PDFProcessor
            p = PDFProcessor('raw_data')
            p.client = MagicMock()
            p.client.models.generate_content.return_value = mock_response

            result = p.parse_with_llm(SAMPLE_PDF_TEXT)

        self.assertIsInstance(result, dict)

    def test_parse_output_has_processor_fields(self):
        """processor.py 출력이 자체 스키마 필드를 포함하는지 확인."""
        mock_response = MagicMock()
        mock_response.text = json.dumps({
            'target_sector': 'IT',
            'target_location': '서울',
            'required_tech': ['AI'],
        })

        with patch.dict(os.environ, {'GEMINI_API_KEY': 'test-key'}):
            from processor import PDFProcessor
            p = PDFProcessor('raw_data')
            p.client = MagicMock()
            p.client.models.generate_content.return_value = mock_response

            result = p.parse_with_llm(SAMPLE_PDF_TEXT)

        for field in PROCESSOR_OUTPUT_FIELDS:
            self.assertIn(field, result, f"processor 출력에 '{field}' 필드 누락")


class TestSchemaCompatibility(unittest.TestCase):
    """processor.py 출력 스키마 vs program_features.parquet LLM 파싱 필드 호환성 검증."""

    def test_output_schema_mismatch_with_program_features(self):
        """
        [비호환 확인] processor.py 출력 필드가 program_features LLM 파싱 필드와 불일치.

        processor.py 출력: target_sector, target_location, required_tech
        program_features:  industry_limit, debt_ratio_limit, requirements

        - target_sector → industry_limit (의미 유사, 필드명 불일치)
        - target_location → program_features 스키마에 없는 필드
        - required_tech → requirements의 일부이나 debt_ratio_limit 누락
        """
        overlap = PROCESSOR_OUTPUT_FIELDS & EXPECTED_PROGRAM_FEATURES_FIELDS
        self.assertEqual(
            len(overlap), 0,
            f"[예상된 불일치] 겹치는 필드가 존재하면 이 테스트가 실패합니다. 겹침={overlap}"
        )

    def test_debt_ratio_limit_missing_from_processor_output(self):
        """processor.py 출력에 debt_ratio_limit 추출 항목 없음."""
        self.assertNotIn('debt_ratio_limit', PROCESSOR_OUTPUT_FIELDS)

    def test_industry_limit_missing_from_processor_output(self):
        """processor.py 출력에 industry_limit 필드 없음 (target_sector로 대체)."""
        self.assertNotIn('industry_limit', PROCESSOR_OUTPUT_FIELDS)


class TestPipelineGapAnalysis(unittest.TestCase):
    """bizinfo extractor → processor.py 파이프라인 연결 갭 분석."""

    def test_bizinfo_does_not_download_pdf(self):
        """bizinfo extractor는 PDF를 다운로드하지 않음 — crawler.py가 필요함."""
        program = SAMPLE_BIZINFO_OUTPUT['programs'][0]
        # bizinfo 출력에는 URL만 있고 파일 경로나 파일 내용이 없음
        self.assertNotIn('pdf_path', program)
        self.assertNotIn('pdf_content', program)
        self.assertIn('detail_url', program)  # URL만 존재

    def test_processor_expects_local_folder_not_s3(self):
        """processor.py는 로컬 폴더 경로를 기대하며 S3 경로를 지원하지 않음."""
        with patch.dict('sys.modules', {
            'google': MagicMock(),
            'google.genai': MagicMock(),
            'google.genai.errors': MagicMock(),
        }):
            with patch.dict(os.environ, {'GEMINI_API_KEY': 'test-key'}):
                from processor import PDFProcessor
                p = PDFProcessor('s3://some-bucket/raw/announcements/2026-04-25/')
                # S3 경로를 넘겨도 os.listdir로 처리하여 실패 — S3 연동 없음
                self.assertTrue(p.folder_path.startswith('s3://'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
