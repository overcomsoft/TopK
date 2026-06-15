"""
SQLite to PostgreSQL 마이그레이션 도구 (DDW2PSQL)

[실행 명령어]
# 일반 실행 (기존 대상 테이블 유지)
python sqlite_to_postgres.py <sqlite_db_path> <host> <port> <user> <password> <dbname>
# 강제 덮어쓰기 실행 (기존 테이블 DROP 후 재구축)
python sqlite_to_postgres.py <sqlite_db_path> <host> <port> <user> <password> <dbname> --replace
python sqlite_to_postgres.py  d:\download\20260602_AIDB_V19.db localhost 5432 postgres dinno DDW_AI_DB --replace
python sqlite_to_postgres.py  d:\download\20260602_AIDB_V19.db 192.168.0.189 5432 dinno  dinno DDW_AI_DB --replace     
python sqlite_to_postgres.py  d:\download\260609_AIDB_v20.db localhost 5432 postgres dinno DDW_AI_DB --replace 
python sqlite_to_postgres.py  d:\download\260609_AIDB_v20.db 192.168.0.189 5432 dinno dinno DDW_AI_DB --replace 
[전체 시스템 프로세스 및 구조]
1. 명령어 파싱: 사용자가 입력한 접속 정보와 마이그레이션 옵션 파싱 (main)
2. DB 자동 생성: 대상 PostgreSQL 서버에 타겟 DB가 없으면 자동 생성 (ensure_pg_database)
3. 스키마 추출 및 보정: SQLite의 테이블 목록 및 PRAGMA table_info를 통해 컬럼 정보를 추출
4. 테이블 DDL 생성: SQLite 유연한 데이터 타입을 PostgreSQL 강타입으로 매핑 및 복합키(PK) 등 제약조건 설정
5. 초고속 벌크 복제: cursor.fetchmany와 psycopg2.extras.execute_values를 이용한 10,000건 단위의 인메모리 배치 이관
6. 인덱스 복구: 정규식을 통한 인덱스 생성 쿼리 보정 및 재생성 (clean_index_sql)

[핵심 알고리즘]
1. Type Casting: map_sqlite_type_to_pg를 통해 문자열 매칭 기반의 1:1 강타입 매핑 지원
2. Default Value 보정: 큰따옴표 이스케이프 및 불리언 플래그(0, 1)의 TRUE/FALSE 문자열 치환
3. PK 가중치 정렬: 다중 컬럼 기본키의 인덱스 가중치 기준 정렬을 통한 제약조건 무결성 유지
4. Batch bulk-loading: 대용량 데이터 로드 시 네트워크 오버헤드와 서버 OOM 방지를 위한 배치 로드
5. Index Regex: SQLite 전용 'COLLATE NOCASE' 삭제 및 식별자 '[ ]' 변환 정규식 처리
"""

import sqlite3
import psycopg2
from psycopg2 import sql, extras
import argparse
import sys
import re

def map_sqlite_type_to_pg(sqlite_type: str) -> str:
    """
    [핵심 함수] 유연한 동적 타입의 엄격한 강타입 매핑 (Type Casting System)
    
    SQLite 타입 문자열의 특징적인 키워드를 매칭하여 호환 및 확장성이 우수한 
    PostgreSQL 최적 데이터 타입으로 매핑하여 반환합니다.
    
    :param sqlite_type: SQLite 원본 테이블 정의서 상의 컬럼 데이터타입 문자열
    :return: 변환된 PostgreSQL 전용 데이터타입 문자열
    """
    # 타입이 명시되지 않았을 경우, 가장 안전한 텍스트로 보존
    if not sqlite_type:
        return "TEXT"
    
    # 비교를 위해 모든 입력 타입을 대문자화하고 좌우 공백을 제거
    t = sqlite_type.upper().strip()
    
    # 1. 정수형 타입 (INTEGER, INT, BIGINT, TINYINT) -> 향후 오버플로우 방지를 위해 BIGINT 대칭 매핑
    if "INT" in t:
        return "BIGINT"
    # 2. 문자열 타입 -> 최대 크기 한계가 없고 공간 낭비가 없는 TEXT로 통일
    if any(x in t for x in ["CHAR", "CLOB", "TEXT", "STR"]):
        return "TEXT"
    # 3. 바이너리 객체(BLOB) -> PostgreSQL의 이진 구조 포맷인 BYTEA 매핑
    if "BLOB" in t:
        return "BYTEA"
    # 4. 부동 소수점 -> 정밀 소수점 보존을 위해 DOUBLE PRECISION 매핑
    if any(x in t for x in ["REAL", "FLOA", "DOUB"]):
        return "DOUBLE PRECISION"
    # 5. 정밀 수치형 -> 정확성이 요구되는 NUMERIC으로 매핑
    if "NUM" in t or "DEC" in t:
        return "NUMERIC"
    # 6. 논리형(Boolean) -> PostgreSQL의 무결성 논리형 BOOLEAN으로 매핑
    if "BOOL" in t:
        return "BOOLEAN"
    # 7. 날짜/시간 -> 표준 타임스탬프로 단일 일관 적용
    if "DATE" in t or "TIME" in t:
        return "TIMESTAMP"
    
    # 기타 패턴 매칭 실패 시 원본 데이터 파괴를 막기 위해 TEXT로 대체
    return "TEXT"

def ensure_pg_database(host: str, port: int, user: str, password: str, dbname: str) -> None:
    """
    [핵심 함수] 타겟 데이터베이스 자동 생성 및 유효성 검사
    
    최종 목적지 데이터베이스 유무를 검사하고, 미존재 시 
    타겟 RDBMS 상에 데이터베이스를 DDL 명령으로 안전하게 동적 자동 생성합니다.
    
    :param host: PostgreSQL 호스트 주소
    :param port: PostgreSQL 포트 번호
    :param user: 관리 권한을 지닌 사용자 계정
    :param password: 계정 비밀번호
    :param dbname: 생성 및 확인할 데이터베이스 이름
    """
    try:
        # CREATE DATABASE 명령은 트랜잭션 블록 내에서 실행될 수 없으므로 
        # 기본 DB인 postgres에 연결한 후 autocommit을 True로 설정하여 강제 실행을 도모합니다.
        conn = psycopg2.connect(host=host, port=port, user=user, password=password, dbname="postgres")
        conn.autocommit = True
        
        with conn.cursor() as cur:
            # 1. 대상 데이터베이스가 pg_database 메타 테이블에 존재하는지 조회
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
            exists = cur.fetchone()
            
            # 2. 존재하지 않는 경우 식별자 객체(sql.Identifier)로 감싸 SQL Injection 예방하며 동적 생성
            if not exists:
                print(f"Database '{dbname}' does not exist. Creating...")
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
                print(f"Database '{dbname}' created successfully.")
            else:
                print(f"Database '{dbname}' already exists.")
                
        # 리소스 반환
        conn.close()
    except Exception as e:
        print(f"Error ensuring database exists: {e}")
        sys.exit(1)

def clean_index_sql(index_name: str, sql_str: str, table_name: str, col_names: list) -> str:
    """
    [핵심 함수] 인덱스(Index) DDL 정밀 정규식 보정 알고리즘
    
    SQLite 전용 인덱스 문법 구문을 정규 표현식을 사용하여 보정함으로써, 
    PostgreSQL 표준 DDL 쿼리로 재정의합니다.
    테이블명, 인덱스명, 컬럼명에 대해 PostgreSQL의 대소문자 구분을 
    보장하기 위해 강제로 큰따옴표(")로 래핑합니다.
    
    :param index_name: 가공할 타겟 인덱스 고유 이름
    :param sql_str: sqlite_master로부터 추출한 수정 전 원본 인덱스 DDL 문자열
    :param table_name: 따옴표 래핑을 위한 대상 테이블 이름
    :param col_names: 따옴표 래핑을 위한 테이블의 모든 컬럼 이름 리스트
    :return: PostgreSQL 환경에서 동작 가능한 보정 완료된 인덱스 DDL 생성문
    """
    if not sql_str:
        return ""
    
    # 정규식 패턴 보정 1: 대소문자 무관하게 1개 이상의 공백 뒤의 'COLLATE NOCASE' 키워드 패턴 삭제
    # (PostgreSQL에서는 이 키워드를 기본적으로 지원하지 않고 구문 에러를 유발합니다)
    cleaned = re.sub(r'(?i)\s+COLLATE\s+NOCASE', '', sql_str)
    
    # 정규식 패턴 보정 2: 대괄호로 묶인 SQLite 식별자 포맷인 [column_name]을 
    # PostgreSQL의 호환 식별자인 "column_name"으로 정교하게 치환
    cleaned = re.sub(r'\[([^\]]+)\]', r'"\1"', cleaned)
    
    # 정규식 패턴 보정 3: 테이블명 강제 따옴표 래핑 (대소문자 구분을 위해)
    pattern_table = re.compile(rf'(?i)(\bON\s+)"?{re.escape(table_name)}"?(\s*\()')
    cleaned = pattern_table.sub(rf'\1"{table_name}"\2', cleaned)
    
    # 정규식 패턴 보정 4: 괄호 안의 컬럼명들에 대해 따옴표 래핑
    match = re.search(r'\((.*)\)', cleaned)
    if match:
        cols_part = match.group(1)
        for col in col_names:
            pattern_col = re.compile(rf'(?i)(?<!")\b{re.escape(col)}\b(?!")')
            cols_part = pattern_col.sub(f'"{col}"', cols_part)
        cleaned = cleaned[:match.start(1)] + cols_part + cleaned[match.end(1):]
        
    # 정규식 패턴 보정 5: 인덱스명 강제 따옴표 래핑
    pattern_idx = re.compile(rf'(?i)(\bINDEX\s+)"?{re.escape(index_name)}"?\b')
    cleaned = pattern_idx.sub(rf'\1"{index_name}"', cleaned)
    
    return cleaned

def migrate_database(sqlite_path: str, host: str, port: int, user: str, password: str, dbname: str, replace: bool) -> None:
    """
    [메인 알고리즘 컨트롤 타워] 전체 데이터베이스 마이그레이션 실행 함수
    
    원본 SQLite DB와 대상 PostgreSQL DB에 동시 세션을 연결하여
    스키마 자동 분석, 테이블/컬럼 재생성, 대용량 데이터 배치 이관, 인덱스 복구 등을 모두 제어합니다.
    
    :param sqlite_path: 원본 SQLite 로컬 데이터베이스 파일 경로
    :param host: 대상 PostgreSQL 호스트
    :param port: 대상 PostgreSQL 포트
    :param user: 대상 PostgreSQL 인증 계정
    :param password: 대상 PostgreSQL 인증 암호
    :param dbname: 대상 PostgreSQL DB
    :param replace: True일 경우 기존 테이블 CASCADE DROP 실행 후 재구축
    """
    try:
        # 1. SQLite 및 PostgreSQL 동시 세션 연결 (ConnBoth 단계)
        sqlite_conn = sqlite3.connect(sqlite_path)
        sqlite_cur = sqlite_conn.cursor()
        
        pg_conn = psycopg2.connect(host=host, port=port, user=user, password=password, dbname=dbname)
        pg_cur = pg_conn.cursor()
        
        # 2. SQLite 마스터 테이블 조회: 시스템 테이블(sqlite_로 시작하는 테이블)을 제외한 사용자 정의 테이블 목록 추출
        sqlite_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        tables = sqlite_cur.fetchall()
        
        # 3. 추출된 각 테이블을 순회하며 개별 마이그레이션 프로세스 진행
        for (table_name,) in tables:
            print(f"\nProcessing table: {table_name}")
            
            # --replace 옵션이 활성화된 경우: 기존 동일 테이블의 종속성까지 연쇄 삭제(CASCADE)
            if replace:
                pg_cur.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(sql.Identifier(table_name)))
            else:
                # 활성화되지 않았고 대상 테이블이 이미 존재하는 경우, 데이터 덮어쓰기 방지를 위해 스킵
                pg_cur.execute("SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=%s)", (table_name.lower(),))
                if pg_cur.fetchone()[0]:
                    print(f"  Table '{table_name}' already exists and --replace not specified. Skipping.")
                    continue
            
            # 4. SQLite PRAGMA table_info 명령을 통해 컬럼 메타데이터(cid, name, type, notnull, dflt_value, pk) 추출
            sqlite_cur.execute(f"PRAGMA table_info(`{table_name}`)")
            columns_raw = sqlite_cur.fetchall()
            
            col_defs = []      # PostgreSQL용 컬럼 정의 DDL 문자열 배열
            pk_cols = []       # 복합 기본키 제약조건 정의를 위해 수집되는 배열 (튜플 형태: pk_weight, col_name)
            bool_indices = []  # 데이터 캐스팅을 위해 BOOLEAN 타입에 해당하는 컬럼의 순서(인덱스) 기록
            
            # 각 컬럼 메타데이터 상세 분석 및 변환 루프
            for col_idx, col in enumerate(columns_raw):
                cid, name, ctype, notnull, dflt_value, pk = col
                
                # 강타입 1:1 변환 알고리즘 호출
                pg_type = map_sqlite_type_to_pg(ctype)
                
                # 변환된 결과가 BOOLEAN 타입일 경우 캐스팅을 위해 해당 컬럼 위치를 캐싱
                if pg_type == "BOOLEAN":
                    bool_indices.append(col_idx)
                
                # sql.Identifier를 이용해 컬럼 식별자 이름에 따옴표 자동 할당 및 포맷팅
                col_def = sql.SQL("{} {}").format(sql.Identifier(name), sql.SQL(pg_type))
                
                # NOT NULL 제약조건 반영
                if notnull:
                    col_def += sql.SQL(" NOT NULL")
                    
                # 기본값(Default Value) 보정 알고리즘
                if dflt_value is not None:
                    # SQLite의 문자열 리터럴인 큰따옴표("value")를 PostgreSQL 표준 홀따옴표('value')로 변환
                    # 또한 내부 홑따옴표가 있을 시 SQL Escape('')를 함께 처리
                    if dflt_value.startswith('"') and dflt_value.endswith('"'):
                        dflt_value = "'" + dflt_value[1:-1].replace("'", "''") + "'"
                    
                    # BOOLEAN 타입의 기본값이 0, 1 문자열로 들어온 경우 표준 예약어로 치환
                    if pg_type == "BOOLEAN":
                        val_clean = dflt_value.strip().strip("'").strip('"').upper()
                        if val_clean in ("1", "TRUE"):
                            dflt_value = "TRUE"
                        elif val_clean in ("0", "FALSE"):
                            dflt_value = "FALSE"
                    
                    # 최종 수정된 기본값 구문 삽입
                    col_def += sql.SQL(" DEFAULT {}").format(sql.SQL(dflt_value))
                
                col_defs.append(col_def)
                
                # 컬럼 정보에서 pk 가중치가 0보다 큰 경우 해당 가중치와 컬럼명 기록
                if pk > 0:
                    pk_cols.append((pk, name))
            
            # 5. 복합 기본키(Composite Primary Key) 정렬 및 제약 구축 알고리즘
            if pk_cols:
                # 다중 기본키 컬럼들의 상대적 정렬 가중치를 오름차순으로 안전하게 복원
                pk_cols.sort()
                pk_names = [name for _, name in pk_cols]
                # PRIMARY KEY ("col1", "col2") 형태의 DDL 결합
                col_defs.append(sql.SQL("PRIMARY KEY ({})").format(
                    sql.SQL(', ').join(map(sql.Identifier, pk_names))
                ))
            
            # DDL 구문 합성 및 PostgreSQL 테이블 최종 생성 명령 실행
            create_table_stmt = sql.SQL("CREATE TABLE {} ({})").format(
                sql.Identifier(table_name),
                sql.SQL(', ').join(col_defs)
            )
            
            print(f"  Creating table {table_name}...")
            pg_cur.execute(create_table_stmt)
            
            # 6. 고속 메모리 세이프 벌크 데이터 이관 알고리즘 (Batch bulk-loading)
            print(f"  Migrating data for {table_name}...")
            sqlite_cur.execute(f"SELECT * FROM `{table_name}`")
            
            batch_size = 10000  # 서버 메모리 고갈 방지를 위한 청크 사이즈 (기본값 10000)
            col_names = [col[1] for col in columns_raw]
            
            # 일괄 삽입용 INSERT INTO 쿼리 문자열 템플릿 준비 (psycopg2.extras 용)
            insert_query = sql.SQL("INSERT INTO {} ({}) VALUES %s").format(
                sql.Identifier(table_name),
                sql.SQL(', ').join(map(sql.Identifier, col_names))
            )
            
            total_rows = 0
            while True:
                # 지정된 배치 사이즈만큼 데이터 끊어 읽기
                rows = sqlite_cur.fetchmany(batch_size)
                if not rows:
                    break  # 모든 데이터 추출 완료
                
                # 파이썬 boolean 캐스팅 변환을 위한 데이터 가공 (타입 충돌 예방)
                if bool_indices:
                    processed_rows = []
                    for row in rows:
                        mut_row = list(row)
                        for idx in bool_indices:
                            val = mut_row[idx]
                            # 데이터가 Null이 아니라면 python 내부 bool 객체로 실시간 변경 (0/1 -> False/True)
                            if val is not None:
                                mut_row[idx] = bool(val)
                        processed_rows.append(tuple(mut_row))
                    rows = processed_rows
                
                # execute_values를 통해 튜플 배열을 일괄로 전송 (다중 행 인서트 쿼리를 한 번에 전송)
                extras.execute_values(pg_cur, insert_query.as_string(pg_conn), rows)
                total_rows += len(rows)
                
            print(f"  Migrated {total_rows} rows.")
            
            # 데이터가 모두 안전하게 로드된 후 첫 트랜잭션 커밋
            pg_conn.commit() 
            
            # 7. 인덱스 복구 및 보정 알고리즘
            print(f"  Recreating indexes for {table_name}...")
            # 현재 테이블에 할당된 모든 인덱스 DDL 쿼리문 추출
            sqlite_cur.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL AND tbl_name=?", (table_name,))
            indexes = sqlite_cur.fetchall()
            
            for idx_name, idx_sql in indexes:
                # 정규식을 통해 호환되지 않는 문법 요소 보정 (테이블명 및 컬럼명 대소문자 래핑 추가)
                cleaned_sql = clean_index_sql(idx_name, idx_sql, table_name, col_names)
                if not cleaned_sql:
                    continue
                try:
                    # 보정된 인덱스 쿼리를 통해 타겟 서버에서 인덱스 재생성
                    pg_cur.execute(cleaned_sql)
                    pg_conn.commit()
                except Exception as e:
                    # 실패 시 콘솔에 경고 메세지만 표시 후 다른 인덱스를 위해 트랜잭션 롤백 (안전 장치)
                    print(f"  Warning: Failed to create index '{idx_name}'. SQL: {cleaned_sql}. Error: {e}")
                    pg_conn.rollback() 
            
        print("\nMigration completed successfully.")
        
    except Exception as e:
        # 예측 불가능한 런타임 오류 시 전체 데이터 보존을 위해 즉각 롤백
        print(f"\nMigration failed: {e}")
        if 'pg_conn' in locals() and pg_conn:
            pg_conn.rollback()
    finally:
        # 8. 자원 반환 (안전 종료 프로세스)
        if 'sqlite_conn' in locals() and sqlite_conn:
            sqlite_conn.close()
        if 'pg_conn' in locals() and pg_conn:
            pg_conn.close()

def main() -> None:
    """
    [진입점] CLI 매개변수 파싱 및 모듈 실행
    
    argparse를 사용해 명령행 인자를 객체 형태로 매핑하고
    DB 생성 함수와 메인 마이그레이션 함수를 순서대로 호출합니다.
    """
    # 1. 인자 처리기 인스턴스화
    parser = argparse.ArgumentParser(description="Migrate SQLite database to PostgreSQL.")
    
    # 필수 파라미터 매핑
    parser.add_argument("sqlite_path", help="원본 위치: Path to the SQLite database file")
    parser.add_argument("host", help="대상 위치: PostgreSQL host")
    parser.add_argument("port", type=int, help="대상 포트: PostgreSQL port")
    parser.add_argument("user", help="인증 계정: PostgreSQL user")
    parser.add_argument("password", help="인증 암호: PostgreSQL password")
    parser.add_argument("dbname", help="대상 DB: PostgreSQL target database name")
    
    # 선택 파라미터 옵션 매핑 (True/False 플래그)
    parser.add_argument("--replace", action="store_true", help="기존 동일 테이블 발견 시 즉시 덮어쓰기(CASCADE DROP)")
    
    # 파싱된 인수를 네임스페이스 객체로 변환
    args = parser.parse_args()
    
    # 2. 실행 파이프라인
    ensure_pg_database(args.host, args.port, args.user, args.password, args.dbname)
    migrate_database(args.sqlite_path, args.host, args.port, args.user, args.password, args.dbname, args.replace)

# 프로그램이 직접 실행되는 경우 main 함수 호출
if __name__ == "__main__":
    main()
