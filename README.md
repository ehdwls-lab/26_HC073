# defect_detection

Active Vision + Structured Light 기반 표면 결함 자동 검사 프로젝트입니다.

## Repository layout

- `src/`: 검사, 카메라, 이상 탐지, 하드웨어 통합 코드
- `tests/`: 하드웨어를 열지 않는 자동 테스트
- `config/`: 운영 설정
- `docs/`: 통합 규약과 운영 문서
- `assets/ui/`: UI 테스트/명시적 참고 모델용 PLY (실행 결과 PLY와 별개)
- `서영 파트 파일/`: 구조광 소스와 현재 배치 보정 데이터
- `3dof_PID_Select/`, `nema34test/`, `sketch_aug20a/`: 펌웨어
- `archive/`: 운영 경로에서 분리한 레거시/정리 대상

촬영 데이터, `results/`, `Log/`, `.venv/`, 학습 중간 가중치는 Git에서 제외합니다.
운영 모델 두 개와 해당 `val.csv`는 의도적으로 관리합니다. 과거 구조광 촬영 산출물은
`archive/pending_cleanup/structured_light_samples/`에 로컬 보존하며 Git 추적만 해제했습니다.

## Setup on another laptop

Python 3.10 기준입니다.

```bash
git clone https://github.com/ehdwls-lab/defect_detection.git
cd defect_detection
python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

실제 카메라/구조광 환경은 추가로 설치합니다.

```bash
python -m pip install -r requirements-hardware.txt
```

관찰용 UI가 필요하면:

```bash
python -m pip install -r requirements-ui.txt
```

## Production 실행

가상환경을 활성화하고 프로젝트 루트에서 실행합니다. 대표 실행 경로는 아래 두 명령입니다.
런처는 운영 검사와 관찰용 UI를 별도 프로세스로 시작합니다.

```bash
python -m src.tools.run_inspection_with_ui --profile gray --ui-3d-mode ply --execute
python -m src.tools.run_inspection_with_ui --profile blue --ui-3d-mode ply --execute
```

실행 전 아래 검증 입력을 배치하고 실제 시리얼 포트·모니터·보정 상태를 확인해야 합니다.
`--execute`는 기존 `EXECUTE` 입력 확인을 거쳐 실제 장비를 실행합니다.
UI는 카메라/시리얼을 열지 않고 공유 메모리 RGB와 현재 검사 run의 완료 manifest PLY만 읽습니다.
구조광 단계는 구조광 자식 프로세스가, 종료 후 검사 단계는 Production이 카메라를 소유합니다.

| Profile | 운영 모델 (각 1.55 MiB) | 정상 검증 manifest |
| --- | --- | --- |
| gray | `models/gray_v2/best_autoencoder.pth` | `data/manifests/gray_v2/val.csv` |
| blue | `models/blue_v1/best_autoencoder.pth` | `data/manifests/blue_v1_rot180/val.csv` |

**검증 RGB/마스크는 별도 배치가 필요합니다.** 각 manifest의 `path`, `mask`는 프로젝트 루트
상대경로입니다. 동일한 원본 4개 RGB와 4개 마스크를 명시된 `results/multiview_normal/...`
위치에 복사하세요. 가중치만으로는 실행할 수 없으며, 원본 정상 검증 이미지로 기존 방식의
threshold를 계산합니다. 입력 목록·checksum과 이식 방법은 [검증 입력 배치 안내](docs/production_artifacts.md)를
참고하세요. 저장소에는 데이터 다운로드 주소를 임의로 지정하지 않았습니다.

## 핵심 소스코드

| 파일 | 역할 · 입력 → 출력 · 시스템 위치 |
| --- | --- |
| [run_inspection_with_ui.py](src/tools/run_inspection_with_ui.py) | profile/CLI를 운영 인자로 변환하고 Production·UI 프로세스 및 공유 메모리 수명을 관리하는 대표 런처입니다. 출력은 독립 검사 세션과 종료 코드입니다. |
| [integrated_inspection_cycle.py](src/integration/integrated_inspection_cycle.py) | 장비 컨트롤러·구조광 결과·검사 설정을 받아 반입, 자세/Z 탐색, 최종 Geometry/ROI/RGB/이상 탐지, 정리를 조율합니다. 시스템 중심 실행부로 `cycle_result.json`과 면별 결과를 생성합니다. |
| [structured_light_runner.py](src/integration/structured_light_runner.py) | 구조광 소스·보정·출력 경로를 검사하고 `물체검사.sh`의 종료까지 기다립니다. 카메라 소유권 경계를 이루며 PLY/pose 산출물 경로와 완료 manifest를 반환합니다. |
| [orbbec_controller.py](src/camera/orbbec_controller.py) | 기존 Gemini 336L 설정/획득 helper로 정렬 RGB·Depth와 intrinsics를 제공합니다. 구조광 종료 후 Production 카메라 owner이며, 미리보기 사용 시 `FramePump`가 획득을 담당합니다. |
| [detector.py](src/anomaly/detector.py) | 가중치·정상 검증 manifest와 최종 RGB/마스크를 기존 AE 추론 API에 전달합니다. 검사 후반부에서 기존 threshold 계산과 64×64/stride 32/coverage 1.0을 유지해 score·판정·heatmap을 출력합니다. |
| [industrial_dashboard.py](src/ui/industrial_dashboard.py) | run 결과/완료 manifest와 공유 RGB를 읽어 단계, 자세, ROI, 판정과 현재 물체 PLY를 표시합니다. 장비 제어를 하지 않는 observer입니다. |

상세 의존성은 [운영 경로 및 정리 audit](docs/repository_cleanup_audit.md)와
[모듈 의존성 목록](docs/production_dependency_graph.json)을 참고하세요.

## 운영 문서

- [카메라 소유권과 FramePump](docs/camera_ownership_preview.md)
- [현재 검사 물체 PLY](docs/current_object_ply.md)
- [최종 ROI 표시](docs/final_roi_visualization.md)
- [Live Preview 및 런처](docs/live_inspection_preview.md)
- [구조광 통합·보정·환경 설정](docs/structured_light_integration.md)
- [시스템 통합 규약](docs/system_integration_contract.md)

## Verification

다음 명령은 실제 하드웨어를 열지 않습니다.

```bash
python -m compileall -q src
python -m unittest discover -s tests -p 'test*.py'
python -m src.tools.run_inspection_with_ui --help
python -m src.tools.run_inspection_with_ui --profile gray --ui-3d-mode ply
python -m src.tools.run_inspection_with_ui --profile blue --ui-3d-mode ply
```

두 dry-run은 정적 설정 검증만 합니다. 모델/검증 입력 로딩, 실제 장비와 UI 렌더링 검증은 포함하지 않습니다.

학습과 이상 탐지 CLI는 패키지 모드로 실행합니다.

```bash
python -m src.train --help
python -m src.infer_anomaly --help
```

실제 하드웨어 실행은 장비별 시리얼 권한, Orbbec SDK, 프로젝터 모니터 배치, 현재 배치 보정값을 확인한 뒤에만 진행하세요. 세부 계약은 `docs/`를 참고하세요.
