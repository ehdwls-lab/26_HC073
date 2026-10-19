# 26_HC073 — Active Vision 표면 결함 검사

Orbbec Gemini 336L, 구조광, 3축 플랫폼, 정상 데이터 기반 Autoencoder를 연결한 한이음 통합 검사 Production 저장소입니다. 이 저장소에는 현재 GRAY/BLUE 검사 실행에 필요한 코드, 모델, 검증 입력과 보정값을 담았습니다.

## 통합 검사 흐름

1. 컨베이어로 검사체를 반입하고 플랫폼을 안전한 자세로 이동합니다.
2. 구조광 패턴을 촬영해 현재 프레임의 3D 형상과 검사 가능 면을 구하고 검사 자세를 계획합니다.
3. 플랫폼 Roll/Pitch를 조절하고 Auto-Z로 Depth 품질과 검사 가능 영역을 확인합니다.
4. 프로젝터를 BLACK으로 전환하고 물리 커버를 닫습니다. LED OFF 상태에서 최종 형상을 확인한 뒤 중립 백색 LED로 Depth ROI와 최종 RGB를 얻습니다.
5. 표면 ROI의 64×64 패치(간격 32)에 Autoencoder 추론을 적용해 결함을 판정하고, 현재 run의 PLY·이미지·결과를 Dashboard에 표시합니다.
6. LED를 끄고 플랫폼을 안전 자세로 복귀시킨 뒤 컨베이어로 검사체를 배출합니다.

검사·제어 코드는 `src/`, 구조광 촬영과 후처리는 `structured_light/`에 있습니다. 핵심 진입점은 `src/tools/run_inspection_with_ui.py`입니다.

## 실행 환경과 설치

개발·실행 환경은 **Ubuntu 22.04, Python 3.10**, X11 화면과 프로젝터가 연결된 장비 PC입니다. 저장소 루트에서 실행합니다.

```bash
git clone https://github.com/ehdwls-lab/26_HC073.git
cd 26_HC073
python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-hardware.txt
python -m pip install -r requirements-ui.txt
```

`requirements-hardware.txt`의 `pyorbbecsdk2` 외에 Orbbec SDK의 호스트 측 구성과 장치 접근 권한을 준비해야 합니다. 구조광 화면 선택에는 `xrandr`와 동작하는 X11 `DISPLAY`가 필요합니다. `bash`는 구조광 shell 실행에 필요합니다. CloudCompare는 선택적 시각화 도구입니다. UI는 PySide6, PLY 렌더러는 PyVista/VTK를 사용합니다.

## 장비와 보정 준비

- RGB-D 카메라: **Orbbec Gemini 336L**. 카메라와 프로젝터의 설치 위치를 보정 당시와 일치시킵니다.
- 3축 플랫폼 MCU: **NUCLEO-F411RE**. PC와 펌웨어는 115200 baud의 `Z:`, `R:`, `P:`, `RST`, `MODE:` 명령 및 `TLM:` 응답을 사용합니다. 실제 보드에 플래시된 버전은 장비에서 별도로 확인해야 합니다.
- 컨베이어 Mega: `/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0`
- 플랫폼 STM32: `/dev/serial/by-id/usb-STMicroelectronics_STM32_STLink_066FFF383133524157152339-if02`
- 조명·프로젝터 커버 Arduino: `/dev/serial/by-id/usb-Arduino__www.arduino.cc__0043_75932313039351C09122-if00`

세 장치의 포트 이름은 해당 PC의 USB 연결에 따라 달라질 수 있습니다. `src/tools/run_inspection_with_ui.py`의 `PORTS`를 확인하고 장비별 실제 경로와 맞추십시오. 커버 매핑은 **OPEN 90°, CLOSE 0°**입니다. 플랫폼 기계적 한계는 Roll/Pitch 각각 ±30°, 운영 자세 한계는 각각 ±25°이며, Z에 따른 결합 기울기 제한을 적용합니다. 안전 범위와 비상 정지 수단을 확인한 뒤에만 실제 실행하십시오.

다음 입력은 현재 광학 배치에 맞는 값이어야 합니다.

| 용도 | 파일 |
|---|---|
| Auto-Z 품질 | `config/automatic_z_quality.json` |
| 프로젝터 범위 | `structured_light/프로젝터 수동 범위 확인/프로젝터_세로범위.json` |
| 빈 플랫폼 Depth | `structured_light/플랫폼 바닥 따기/현재배치_기준데이터/active/E1999_G64/플랫폼_바닥_depth.npy` |

## 모델과 Validation

추론 시작 시 해당 모델과 정상 Validation 입력에서 threshold를 계산합니다. CSV가 가리키는 RGB와 `inspection_mask.png`가 모두 필요합니다.

| 프로필 | 활성 모델 | Validation CSV | 이미지·마스크 |
|---|---|---|---|
| GRAY | `models/gray_v2/best_autoencoder.pth` | `data/manifests/gray_v2/val.csv` | `data/production_validation/gray_v2/pose_08,09/` |
| BLUE | `models/blue_v1/best_autoencoder.pth` | `data/manifests/blue_v1_rot180/val.csv` | `data/production_validation/blue_v1_rot180/pose_08,09/` |

## 실행

장비를 열지 않는 설정 점검:

```bash
python -m src.tools.run_inspection_with_ui --profile gray --ui-3d-mode ply
python -m src.tools.run_inspection_with_ui --profile blue --ui-3d-mode ply
```

실제 통합 검사:

```bash
python -m src.tools.run_inspection_with_ui --profile gray --ui-3d-mode ply --execute
python -m src.tools.run_inspection_with_ui --profile blue --ui-3d-mode ply --execute
```

`--execute`는 컨베이어, 플랫폼, 카메라, 프로젝터와 조명을 실제로 동작시키며 실행 전 터미널 확인을 요구합니다. 기본 결과 경로는 저장소 아래 `results/integrated_hardware/`입니다. `results/`가 없는 clone에서도 실행 시 생성됩니다. 다른 위치를 쓰려면 `--output-root`를 지정하십시오. 검사 run의 구조광 raw·PLY, final RGB·Depth·ROI, anomaly 결과와 로그도 해당 run 아래에 생성됩니다. UI는 같은 run의 현재 산출물을 읽습니다. 결과 데이터는 GitHub에 포함되지 않습니다.

## 알려진 제한사항

- 실제 검사는 Ubuntu 22.04의 장비, X11 프로젝터, Orbbec SDK, USB 장치 권한 및 유효한 보정값이 필요합니다. dry-run은 이들 장비의 동작을 검증하지 않습니다.
- 저장소의 모델과 Validation 입력은 현재 GRAY/BLUE 프로필용입니다. 광학 배치나 검사 재질이 바뀌면 보정과 모델 적합성을 다시 확인해야 합니다.
- 기본 serial by-id 경로는 현재 장비 기준이며 다른 PC에서 장치 ID가 달라지면 설정을 맞춰야 합니다.
- MCU 펌웨어 소스와 개발·학습 자료는 별도 보관 대상이며 이 Production 저장소에 포함되지 않습니다.
