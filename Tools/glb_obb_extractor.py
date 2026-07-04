# -*- coding: utf-8 -*-
"""
glb_obb_extractor.py
--------------------
설명: GLB 포맷의 3차원 모델 파일로부터 개별 메쉬 또는 전체 씬의 Oriented Bounding Box(OBB)를 추출하는 모듈입니다.
사용 예시:
    from Tools.glb_obb_extractor import extract_mesh_obbs, extract_global_obb
    
    # 1. 개별 메쉬별 OBB 추출
    obbs = extract_mesh_obbs("data/lattice.glb")
    for obb in obbs:
        print(obb.center, obb.extents)
        
    # 2. 전체 모델의 단일 global OBB 추출
    global_obb = extract_global_obb("data/lattice.glb")
    print(global_obb.center, global_obb.extents)
"""

import os
import trimesh
import numpy as np
import logging

# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

class OBBData:
    """
    Oriented Bounding Box (OBB) 정보를 저장하는 클래스입니다.
    모든 numpy 배열 데이터는 JSON 직렬화 및 직관적 사용을 위해 Python 기본 리스트(list) 형태로 제공됩니다.
    """
    def __init__(self, center, extents, rotation, transform, vertices, name=None):
        self.center = list(center)          # [cx, cy, cz]
        self.extents = list(extents)        # [dx, dy, dz] (local 가로, 세로, 높이 크기)
        self.rotation = [list(row) for row in rotation]  # 3x3 회전 행렬
        self.transform = [list(row) for row in transform] # 4x4 동차 변환 행렬 (Rotation + Translation)
        self.vertices = [list(v) for v in vertices]      # 8개 꼭짓점 좌표 리스트
        self.name = name                    # 객체/메쉬의 이름 (존재할 경우)

    def to_dict(self):
        """
        딕셔너리 형태로 OBB 데이터를 반환합니다 (JSON 직렬화용).
        """
        return {
            "name": self.name,
            "center": self.center,
            "extents": self.extents,
            "rotation": self.rotation,
            "transform": self.transform,
            "vertices": self.vertices
        }

    def __repr__(self):
        return f"OBBData(name={self.name}, center={self.center}, extents={self.extents})"


def _pad_obb_if_flat(obb_box, min_thickness: float = 50.0):
    """
    OBB의 extents 중 min_thickness보다 작은 값이 있으면 해당 방향의 크기를 min_thickness로 늘려줍니다.
    """
    extents = obb_box.extents.copy()
    padded = False
    for i in range(3):
        if extents[i] < min_thickness:
            extents[i] = min_thickness
            padded = True
    if padded:
        return trimesh.primitives.Box(extents=extents, transform=obb_box.transform)
    return obb_box


def _convert_obb_primitive(obb_box, name=None) -> OBBData:
    """
    trimesh.primitives.Box 형태의 OBB 객체로부터 OBBData 인스턴스를 빌드합니다.
    """
    transform = obb_box.transform
    rotation = transform[:3, :3]
    center = obb_box.centroid
    extents = obb_box.extents
    vertices = obb_box.vertices
    
    return OBBData(
        center=center,
        extents=extents,
        rotation=rotation,
        transform=transform,
        vertices=vertices,
        name=name
    )


def extract_mesh_obbs(glb_path: str, split_components: bool = True, min_thickness: float = 50.0) -> list[OBBData]:
    """
    GLB 파일 내에 존재하는 모든 개별 메쉬(컴포넌트)의 Oriented Bounding Box(OBB) 리스트를 추출합니다.
    씬의 변환 그래프(Transform Graph)를 반영하여 월드 좌표계(World Coordinates) 기준으로 계산됩니다.
    
    Args:
        glb_path (str): GLB 파일의 절대 또는 상대 경로
        split_components (bool): True일 경우 메쉬를 연결된 독립된 솔리드 구성요소(삼각형 단위 위상 연결성)들로 분할하여 OBB를 계산합니다.
        min_thickness (float): OBB 상자의 최소 두께(mm). 너무 얇은 평면 형태의 OBB가 생성되는 것을 방지합니다.
        
    Returns:
        list[OBBData]: 각 개별 메쉬 및 분할된 컴포넌트의 OBBData 리스트
    """
    if not os.path.exists(glb_path):
        raise FileNotFoundError(f"GLB 파일을 찾을 수 없습니다: {glb_path}")
        
    logger.info(f"GLB 파일 로드 중: {glb_path}")
    scene = trimesh.load(glb_path)
    
    # 씬/메쉬 구조 분해
    if isinstance(scene, trimesh.Scene):
        # scene.dump(concatenate=False)는 모든 노드의 변환(Transform)을 적용한 개별 Trimesh 목록을 반환합니다.
        try:
            meshes = scene.dump(concatenate=False)
        except Exception as e:
            logger.error(f"Scene dump 실패, 개별 지오메트리 단위로 탐색합니다. 오류: {e}")
            meshes = list(scene.geometry.values())
    elif isinstance(scene, trimesh.Trimesh):
        meshes = [scene]
    else:
        logger.warning(f"지원되지 않는 mesh 타입입니다: {type(scene)}")
        return []
        
    obb_list = []
    for idx, mesh in enumerate(meshes):
        # 정점(vertex) 정보가 없는 빈 메쉬는 생략
        if len(mesh.vertices) == 0:
            continue
            
        mesh_name = getattr(mesh, 'metadata', {}).get('name', f"mesh_{idx}")
        if not mesh_name and hasattr(mesh, 'name'):
            mesh_name = mesh.name
            
        # 메쉬 분할 옵션 처리 (삼각형 단위 위상 분할)
        if split_components:
            try:
                # only_watertight=False로 지정하여 열린 쉘 구조나 복잡한 격자 구조도 분할이 가능하도록 합니다.
                components = mesh.split(only_watertight=False)
                # 만약 반환된 컴포넌트 목록이 비어있다면 원본 메쉬를 사용합니다.
                if not components:
                    components = [mesh]
            except Exception as e:
                logger.warning(f"메쉬 '{mesh_name}' 분할 실패: {e}")
                components = [mesh]
        else:
            components = [mesh]
            
        for c_idx, comp in enumerate(components):
            # 정점 수가 부족한 유효하지 않은 컴포넌트 제외
            if len(comp.vertices) < 3:
                continue
            try:
                comp_name = f"{mesh_name}_part_{c_idx}" if len(components) > 1 else mesh_name
                
                # 1. OBB 계산 시도
                obb_box = None
                try:
                    obb_box = comp.bounding_box_oriented
                except Exception:
                    # 2. Convex Hull 등 오류 발생 시 미세 지터 적용 후 재시도
                    try:
                        comp_jittered = comp.copy()
                        noise = np.random.uniform(-0.05, 0.05, size=comp_jittered.vertices.shape)
                        comp_jittered.vertices += noise
                        obb_box = comp_jittered.bounding_box_oriented
                    except Exception as je:
                        logger.warning(f"컴포넌트 '{comp_name}'의 OBB 계산(지터링 포함) 실패: {je}")
                        continue
                
                if obb_box is not None:
                    # 최소 두께 적용 (얇은 면 방지)
                    obb_box = _pad_obb_if_flat(obb_box, min_thickness=min_thickness)
                    obb_data = _convert_obb_primitive(obb_box, name=comp_name)
                    obb_list.append(obb_data)
            except Exception as e:
                logger.warning(f"컴포넌트 '{comp_name}'의 OBB 계산/변환 처리 중 예외 발생: {e}")
            
    logger.info(f"성공적으로 {len(obb_list)}개의 메쉬 OBB를 추출하였습니다.")
    return obb_list


def extract_global_obb(glb_path: str, min_thickness: float = 50.0) -> OBBData:
    """
    GLB 파일 내의 모든 메쉬 데이터를 하나로 통합하여, 씬 전체를 감싸는 단일 global Oriented Bounding Box(OBB)를 추출합니다.
    
    Args:
        glb_path (str): GLB 파일의 절대 또는 상대 경로
        min_thickness (float): OBB 상자의 최소 두께(mm).
        
    Returns:
        OBBData: 전체 씬의 단일 global OBBData
    """
    if not os.path.exists(glb_path):
        raise FileNotFoundError(f"GLB 파일을 찾을 수 없습니다: {glb_path}")
        
    logger.info(f"GLB 파일 로드 및 병합 중: {glb_path}")
    scene = trimesh.load(glb_path)
    
    if isinstance(scene, trimesh.Scene):
        # 전체 지오메트리를 하나의 Trimesh 객체로 결합(월드 좌표계 반영)
        combined_mesh = scene.to_geometry()
    elif isinstance(scene, trimesh.Trimesh):
        combined_mesh = scene
    else:
        raise ValueError(f"지원되지 않는 mesh 타입입니다: {type(scene)}")
        
    if len(combined_mesh.vertices) == 0:
        raise ValueError("모델 내에 유효한 vertex 데이터가 존재하지 않습니다.")
        
    logger.info("통합 메쉬의 global OBB 계산 중...")
    obb_box = None
    try:
        obb_box = combined_mesh.bounding_box_oriented
    except Exception:
        try:
            combined_mesh_jittered = combined_mesh.copy()
            noise = np.random.uniform(-0.05, 0.05, size=combined_mesh_jittered.vertices.shape)
            combined_mesh_jittered.vertices += noise
            obb_box = combined_mesh_jittered.bounding_box_oriented
        except Exception as e:
            raise ValueError(f"Global OBB 계산 실패: {e}")
            
    obb_box = _pad_obb_if_flat(obb_box, min_thickness=min_thickness)
    global_obb = _convert_obb_primitive(obb_box, name="global_scene_obb")
    
    logger.info("Global OBB 추출을 완료하였습니다.")
    return global_obb


def save_obb_to_glb(glb_path: str, output_path: str = None, split_components: bool = True, min_thickness: float = 50.0) -> str:
    """
    GLB 파일로부터 OBB(Oriented Bounding Box)들을 추출하여 새 GLB 파일로 저장합니다.
    
    Args:
        glb_path (str): 입력 GLB 파일 경로
        output_path (str, optional): 출력할 OBB GLB 파일 경로. 지정하지 않을 경우 기존 파일명에 '_obb.glb'를 붙여 생성합니다.
        split_components (bool): True일 경우 메쉬를 연결된 개별 솔리드 부품으로 나누어 OBB를 구합니다.
        min_thickness (float): OBB 상자의 최소 두께(mm).
        
    Returns:
        str: 저장된 OBB GLB 파일 경로
    """
    if not os.path.exists(glb_path):
        raise FileNotFoundError(f"GLB 파일을 찾을 수 없습니다: {glb_path}")
        
    if output_path is None:
        input_dir, input_file = os.path.split(glb_path)
        basename, _ = os.path.splitext(input_file)
        output_path = os.path.join(input_dir, f"{basename}_obb.glb")
        
    logger.info(f"OBB GLB 변환 시작: {glb_path} -> {output_path}")
    scene = trimesh.load(glb_path)
    
    if isinstance(scene, trimesh.Scene):
        meshes = scene.dump(concatenate=False)
    elif isinstance(scene, trimesh.Trimesh):
        meshes = [scene]
    else:
        raise ValueError(f"지원되지 않는 mesh 타입입니다: {type(scene)}")
        
    obb_meshes = []
    for idx, mesh in enumerate(meshes):
        if len(mesh.vertices) == 0:
            continue
            
        if split_components:
            try:
                components = mesh.split(only_watertight=False)
                if not components:
                    components = [mesh]
            except Exception as e:
                logger.warning(f"메쉬 분할 실패: {e}")
                components = [mesh]
        else:
            components = [mesh]
            
        for comp in components:
            if len(comp.vertices) < 3:
                continue
            try:
                obb_box = None
                try:
                    obb_box = comp.bounding_box_oriented
                except Exception:
                    try:
                        comp_jittered = comp.copy()
                        noise = np.random.uniform(-0.05, 0.05, size=comp_jittered.vertices.shape)
                        comp_jittered.vertices += noise
                        obb_box = comp_jittered.bounding_box_oriented
                    except Exception:
                        pass
                
                if obb_box is not None:
                    obb_box = _pad_obb_if_flat(obb_box, min_thickness=min_thickness)
                    # trimesh.primitives.Box는 Trimesh의 서브클래스이므로 씬에 추가 및 GLB 저장 가능
                    obb_meshes.append(obb_box)
            except Exception as e:
                pass
                
    if not obb_meshes:
        raise ValueError("유효한 OBB 박스를 생성할 수 없습니다.")
        
    # OBB 박스들로 구성된 새 Scene 생성
    obb_scene = trimesh.Scene(obb_meshes)
    
    # 출력 폴더 생성 확인
    output_dir = os.path.dirname(os.path.abspath(output_path))
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)
        
    obb_scene.export(output_path)
    logger.info(f"성공적으로 {len(obb_meshes)}개의 OBB 박스를 포함한 GLB 파일을 저장했습니다: {output_path}")
    return output_path


if __name__ == "__main__":
    # 모듈 자체 실행 시 간단한 기능 검증 진행
    import sys
    
    test_file = r"data/lattice.glb"
    if len(sys.argv) > 1:
        test_file = sys.argv[1]
        
    if os.path.exists(test_file):
        print(f"=== 테스트 파일: {test_file} ===")
        try:
            print("\n1. 개별 OBB 추출 테스트:")
            obbs = extract_mesh_obbs(test_file)
            print(f"추출된 OBB 개수: {len(obbs)}")
            if obbs:
                print(f"첫 번째 OBB 정보:")
                print(f"  이름: {obbs[0].name}")
                print(f"  중심점: {obbs[0].center}")
                print(f"  크기(Extents): {obbs[0].extents}")
                print(f"  8개 꼭짓점:\n{np.array(obbs[0].vertices)}")
                
            print("\n2. Global OBB 추출 테스트:")
            g_obb = extract_global_obb(test_file)
            print(f"Global OBB 정보:")
            print(f"  이름: {g_obb.name}")
            print(f"  중심점: {g_obb.center}")
            print(f"  크기(Extents): {g_obb.extents}")
            
        except Exception as e:
            print(f"에러 발생: {e}")
    else:
        print(f"테스트용 GLB 파일({test_file})을 찾을 수 없습니다. 경로를 확인해주세요.")
