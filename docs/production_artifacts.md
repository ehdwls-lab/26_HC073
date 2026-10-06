# Production 모델 / 검증 입력 배치

운영 모델 두 개는 각각 1,630,201 bytes (약 1.55 MiB)이며 일반 Git으로 유지한다.
기존 가중치를 변경하지 않았다. 아래 checksum은 이 작업 트리의 원본 기준이다.

| 모델 | SHA256 |
| --- | --- |
| `models/gray_v2/best_autoencoder.pth` | `6a721af813ff40aa48c6c2deab9893bb876ce85670f15ea1ab908da44c6d77ba` |
| `models/blue_v1/best_autoencoder.pth` | `96789af8c4a7119505d6c17f1ea851b4f444f129374ee37eb2e9ed85c3c6fbca` |

## 외부 정상 validation 데이터

`data/manifests/gray_v2/val.csv`와 `data/manifests/blue_v1_rot180/val.csv`는 Git 관리 대상이다.
각각 원래 정상 검증 pose_08/pose_09의 RGB와 마스크를 가리킨다. split/label/pose/coverage와 이미지 내용은 바꾸지 않았다.
`src.infer_anomaly.read_validation_entries()`는 상대경로를 프로젝트 root 기준으로 해석한다.

아래 8개 파일을 기존 장비에서 복사하거나 팀이 승인한 저장소에서 받아 **프로젝트 root 기준 동일 경로**에 배치한다.
이미지는 전처리하지 않고 원본 그대로 복사한다. `results/`는 Git에서 제외된다.
공개 다운로드 주소는 아직 정해지지 않았으며 모델만 clone해서는 threshold를 준비할 수 없다.

| Profile / 용도 | 프로젝트 상대경로 | bytes | SHA256 |
| --- | --- | ---: | --- |
| gray_v2 / path | `results/multiview_normal/GRAY_01/20260904_181448/pose_08/final_rgb.png` | 1460930 | `1f410eb5b1da2919712ddfc2a8c49f13069b5b48ad2983d6e96de418ffd1af03` |
| gray_v2 / mask | `results/multiview_normal/GRAY_01/20260904_181448/pose_08/inspection_mask.png` | 4817 | `4e67c405102e1b264c1bcf178f8f951e0c2e694d5d50a07d6523ba2611d120f6` |
| gray_v2 / path | `results/multiview_normal/GRAY_01/20260904_181448/pose_09/final_rgb.png` | 1597748 | `57326062db4f2c7cd0b83f06bf5951213faf6b425e3425a6b085f0732b372d00` |
| gray_v2 / mask | `results/multiview_normal/GRAY_01/20260904_181448/pose_09/inspection_mask.png` | 4266 | `8ef2b9ed4d4fc2d0ee0aba4e86adc75de4b93c230f319c5ef933805a9cecd1a9` |
| blue_v1_rot180 / path | `results/multiview_normal/BLUE_01_ROT180/20260904_184605/pose_08/final_rgb.png` | 1406233 | `8af5dfae50db5a5561b89c74889cdd1243314848b9213a0c317eadec541ddd3e` |
| blue_v1_rot180 / mask | `results/multiview_normal/BLUE_01_ROT180/20260904_184605/pose_08/inspection_mask.png` | 5097 | `af6be7ad574040f93ec28c3b809606e0b894725386d4ce45b9c71bc3b881271d` |
| blue_v1_rot180 / path | `results/multiview_normal/BLUE_01_ROT180/20260904_184605/pose_09/final_rgb.png` | 1492028 | `52eb9ef91b6b0b1f12fd37c406aafe8e6ddcc395b84999c02865037e50e26432` |
| blue_v1_rot180 / mask | `results/multiview_normal/BLUE_01_ROT180/20260904_184605/pose_09/inspection_mask.png` | 4877 | `acf63eca938e2c08261cd3880c3f9f3226e733b1ddebf551f7d7396cb44ad54c` |

threshold는 기존과 동일하게 정상 validation patch score의 percentile로 실행 시 계산한다.
입력 이미지를 대체하거나 샘플을 추가/삭제하면 threshold가 달라지므로 원본을 유지한다.
GRAY_02/03은 hold-out TEST 전용이며 이 배치에 포함하지 않는다.

구조광 보정 입력은 `서영 파트 파일/프로젝터 수동 범위 확인/프로젝터_세로범위.json`과
`서영 파트 파일/플랫폼 바닥 따기/현재배치_기준데이터/active/`에 Git으로 보존한다.
다른 장비에서 기존 보정값이 맞는지는 사람이 확인해야 한다. 이 cleanup은 재보정하지 않는다.

## 하드웨어 없이 배치 확인

가상환경을 활성화하고 프로젝트 root에서 아래 명령을 실행하면 모델/threshold만 준비한다.
카메라·시리얼·모터는 열지 않는다. 이 명령은 dry-run보다 강한 검사이며 torch 연산 시간이 걸릴 수 있다.

```bash
python - <<'PY'
from pathlib import Path
from src.anomaly.detector import ProductionAnomalyConfig, ProductionAnomalyDetector
for model, manifest in [('gray_v2', 'gray_v2'), ('blue_v1', 'blue_v1_rot180')]:
    detector = ProductionAnomalyDetector(ProductionAnomalyConfig(
        checkpoint_path=Path('models') / model / 'best_autoencoder.pth',
        validation_manifest_path=Path('data/manifests') / manifest / 'val.csv',
    ))
    detector.prepare()
    print(model, 'model/validation ready')
PY
```

`--help`와 profile dry-run은 정적 설정만 검증하므로 validation 데이터 누락을 검사하지 않는다.
실제 시연은 별도의 장비 검증과 기존 EXECUTE 확인을 거친다.
