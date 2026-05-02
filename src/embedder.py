import os
import shutil

from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document

class PolicyVectorStore:
    def __init__(self, persist_directory="./chroma_db", reset=False):
        # 임베딩 모델 세팅
        print("임베딩 모델 로딩 중... (snunlp/KR-SBERT-V40K-klueNLI-augSTS)")
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
            print(f"기존 DB({self.persist_directory})를 초기화했습니다.")

        self.db = Chroma(
            collection_name="policy",
            embedding_function=self.embeddings,
            persist_directory=self.persist_directory
        )
        
    def add_policies(self, json_data: list[dict]):
        """processor.py에서 파싱된 JSON 데이터를 Vector DB에 넣기"""
        documents = []
        for item in json_data:
            # 특허/기술력 매칭을 위한 텍스트 구성 (예시)
            page_content = f"사업명: {item['program_name']}\n지원자격 및 기술요건: {item['requirements']}"

            # 메타데이터에 원본 정보 저장
            metadata = {
                "program_id": item['program_id'],
                "category": item['category'],
            }
            documents.append(Document(page_content=page_content, metadata=metadata))

        self.db.add_documents(documents)
        print(f"{len(documents)}개의 공문이 DB에 저장되었습니다.")

    def search_with_soft_filter(self, query: str, threshold: float = 0.8) -> list[Document]:
        """Soft Filter 적용 (유사도 0.8 이상만 통과)"""
        print(f"검색어: '{query}' (임계값: {threshold})")

        # 코사인 유사도 점수와 함께 검색 (L2 거리 기준의 경우 변환 필요)
        results = self.db.similarity_search_with_relevance_scores(query, k=5)

        passed_docs = []
        for doc, score in results:
            if score >= threshold:
                passed_docs.append((doc, score))

        print(f"결과: 총 {len(results)}개 검색됨, 그 중 Soft Filter({threshold}) 통과: {len(passed_docs)}개")
        return passed_docs
    
if __name__ == "__main__":
    # 테스트용 가상 데이터 (나중에 processor.py 결과로 교체)
    dummy_data = [
        {
            "program_id": "P001",
            "program_name": "우수 특허 사업화 지원",
            "category": "기술",
            "requirements": "특허를 보유한 기술 기반 창업 기업"
        },
        {
            "program_id": "P002",
            "program_name": "일반 소상공인 융자",
            "category": "자금",
            "requirements": "매출액 50억 미만 일반 소상공인"
        }
    ]

    store = PolicyVectorStore(reset=True)
    store.add_policies(dummy_data)
    
    # 에이전트가 던질 예상 질문 (예: 사용자의 특허 정보)
    query = "우수한 특허를 보유한 기술 기반 창업 기업 지원"

    # 0.8 필터링 작동 확인
    filtered_results = store.search_with_soft_filter(query, threshold=0.8)

    for doc, score in filtered_results:
        status = "[통과]" if score >= 0.8 else "[미통과]"
        print(f"\n{status} 사업명: {doc.metadata['program_id']} (점수: {score:.3f})")