"""중진공 정책자금 ETL 파이프라인 — Airflow DAG.

DAG 구조:
    [extract_dart, extract_kipris, extract_bizinfo]
        >> transform_merge
        >> load_welfare

스케줄: @weekly
"""
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator

from dags.extractors.dart_extractor import extract_dart_task
from dags.extractors.kipris_extractor import extract_kipris_task
from dags.extractors.bizinfo_extractor import extract_bizinfo_task
from dags.extractors.welfare_loader import load_welfare_task
from dags.transformers.merge import merge_task

default_args = {
    'owner': 'dongjin',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 2,
    'retry_delay': timedelta(minutes=5),
}

with DAG(
    dag_id='etl_pipeline',
    description='중진공 정책자금 ETL 파이프라인',
    default_args=default_args,
    schedule_interval='@weekly',
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=['etl', 'policy-fund'],
) as dag:

    extract_dart = PythonOperator(
        task_id='extract_dart',
        python_callable=extract_dart_task,
        op_kwargs={'year': None},  # None → 전년도 기준
    )

    extract_kipris = PythonOperator(
        task_id='extract_kipris',
        python_callable=extract_kipris_task,
        op_kwargs={'corp_names': None},  # None → DART에서 수집된 기업 목록 사용
    )

    extract_bizinfo = PythonOperator(
        task_id='extract_bizinfo',
        python_callable=extract_bizinfo_task,
    )

    transform_merge = PythonOperator(
        task_id='transform_merge',
        python_callable=merge_task,
        op_kwargs={'date_str': None},  # None → 오늘 날짜 기준
    )

    load_welfare = PythonOperator(
        task_id='load_welfare',
        python_callable=load_welfare_task,
    )

    # 병렬 extract → 순차 transform → load
    [extract_dart, extract_kipris, extract_bizinfo] >> transform_merge >> load_welfare
