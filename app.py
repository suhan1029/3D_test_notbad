import streamlit as st
import requests
from dotenv import load_dotenv
import os
import base64
import time
import io
import trimesh

# ----------------------------
# 초기 설정
# ----------------------------
load_dotenv()
MESHY_API_KEY = os.environ.get("MESHY_API_KEY")
API_URL = "https://api.meshy.ai/openapi/v1/image-to-3d"

st.set_page_config(page_title="Image → 3D (Meshy)", layout="wide")
st.title("🖼️ → 🧊 Image to 3D (Meshy.ai)")

# 세션 상태
if "task_id" not in st.session_state:
    st.session_state.task_id = None
if "model_url" not in st.session_state:
    st.session_state.model_url = None
if "glb_bytes" not in st.session_state:
    st.session_state.glb_bytes = None
if "start_time" not in st.session_state:
    st.session_state.start_time = None

left, right = st.columns([1, 1])

with left:
    uploaded_file = st.file_uploader("이미지를 업로드하세요 (jpg/png)", type=["jpg", "jpeg", "png"])

with right:
    st.markdown("**설정**")
    enable_pbr = st.checkbox("Enable PBR", value=True)
    should_remesh = st.checkbox("Remesh", value=True)
    should_texture = st.checkbox("Texture", value=True)

run_button = st.button("🚀 3D 변환 시작")

# ----------------------------
# 변환 시작: 이미지 업로드 → Meshy 요청
# ----------------------------
if run_button:
    if not MESHY_API_KEY:
        st.error("MESHY_API_KEY 가 설정되지 않았습니다. .env 파일에 키를 추가해주세요.")
        st.stop()
    if uploaded_file is None:
        st.warning("먼저 이미지를 업로드해주세요.")
        st.stop()

    # 파일 원본 바이트와 미리보기 준비
    img_bytes = uploaded_file.getvalue()
    img_b64 = base64.b64encode(img_bytes).decode("utf-8")

    # 좌우 비교 레이아웃
    view_left, view_right = st.columns(2)
    with view_left:
        st.subheader("원본 이미지")
        st.image(img_bytes, use_container_width=True)

    # Meshy 요청
    payload = {
        "image_url": f"data:image/png;base64,{img_b64}",
        "enable_pbr": enable_pbr,
        "should_remesh": should_remesh,
        "should_texture": should_texture,
    }
    headers = {"Authorization": f"Bearer {MESHY_API_KEY}"}

    with st.status("Meshy에 작업 생성 중...", expanded=False) as status:
        try:
            resp = requests.post(API_URL, headers=headers, json=payload, timeout=60)
            resp.raise_for_status()
            task_id = resp.json()["result"]
            st.session_state.task_id = task_id
            status.update(label=f"작업 생성 완료 (task_id: {task_id})", state="complete")
        except Exception as e:
            st.error(f"작업 생성 실패: {e}")
            st.stop()

    # 진행바 + 폴링 로직
    #  - Phase 1: 0~4분(240초) 동안 시간 기반 진행 (0→90%)
    #  - Phase 2: 4분 이후 5초 간격 폴링, 완료되면 100% 즉시 채움
    progress_holder = st.empty()
    info_holder = st.empty()

    progress = progress_holder.progress(0, text="모델 생성 준비 중...")
    phase1_duration = 240  # 4분
    phase1_target = 0.90   # 90%
    poll_interval_phase1 = 1.0
    poll_interval_phase2 = 5.0

    headers = {"Authorization": f"Bearer {MESHY_API_KEY}"}

    start_time = time.time()
    st.session_state.start_time = start_time
    model_url = None

    # Phase 1: 시간 기반 진행 (0~240초)
    while True:
        elapsed = time.time() - start_time
        if elapsed >= phase1_duration:
            break

        # 진행률 계산 (0 ~ 90%)
        ratio = min(elapsed / phase1_duration, 1.0)
        pct = int(ratio * phase1_target * 100)
        progress.progress(pct, text=f"3D 변환 중... (약 4분 예상, 경과 {int(elapsed)}초)")

        # 15초마다 가볍게 상태 체크(선제 완료 대비)
        if int(elapsed) % 15 == 0 and elapsed > 0:
            try:
                status_resp = requests.get(f"{API_URL}/{st.session_state.task_id}", headers=headers, timeout=30)
                status_resp.raise_for_status()
                data = status_resp.json()
                if data.get("status") == "SUCCEEDED":
                    model_url = data["model_urls"]["glb"]
                    progress.progress(100, text="완료! 모델을 불러오는 중...")
                    break
                elif data.get("status") == "FAILED":
                    progress.progress(pct, text="변환 실패")
                    st.error("3D 변환이 실패했습니다.")
                    st.stop()
            except Exception:
                pass

        time.sleep(poll_interval_phase1)

    # Phase 2: 4분 이후 5초 간격 폴링
    if model_url is None:
        # 95%로 고정해두고 폴링
        progress.progress(95, text="마무리 중... (5초마다 확인)")
        while True:
            try:
                status_resp = requests.get(f"{API_URL}/{st.session_state.task_id}", headers=headers, timeout=30)
                status_resp.raise_for_status()
                data = status_resp.json()
                status = data.get("status")
                if status == "SUCCEEDED":
                    model_url = data["model_urls"]["glb"]
                    progress.progress(100, text="완료! 모델을 불러오는 중...")
                    break
                elif status == "FAILED":
                    st.error("3D 변환이 실패했습니다.")
                    st.stop()
                else:
                    # 진행 유지
                    pass
            except Exception:
                # 네트워크 일시 오류 등은 무시하고 재시도
                pass
            time.sleep(poll_interval_phase2)

    # 모델(GLB) 다운로드
    try:
        glb_bytes = requests.get(model_url, timeout=180).content
        st.session_state.model_url = model_url
        st.session_state.glb_bytes = glb_bytes
    except Exception as e:
        st.error(f"GLB 다운로드 실패: {e}")
        st.stop()

    # ----------------------------
    # 결과 표시: 좌우 비교 + 다운로드/변환
    # ----------------------------
    view_left, view_right = st.columns(2)

    with view_left:
        st.subheader("원본 이미지")
        st.image(img_bytes, use_container_width=True)

    with view_right:
        st.subheader("생성된 3D 미리보기 (GLB)")

        # model-viewer 로 임베드
        glb_b64 = base64.b64encode(st.session_state.glb_bytes).decode()
        st.components.v1.html(
            f"""
            <script type="module" src="https://unpkg.com/@google/model-viewer/dist/model-viewer.min.js"></script>
            <model-viewer src="data:model/gltf-binary;base64,{glb_b64}"
                          alt="Generated 3D Model"
                          auto-rotate
                          camera-controls
                          ar
                          style="width:100%;height:520px;background:#111;border-radius:12px;">
            </model-viewer>
            """,
            height=540,
        )

    # 다운로드 섹션
    st.markdown("### 📥 다운로드")
    st.download_button(
        label="GLB 다운로드",
        data=st.session_state.glb_bytes,
        file_name="model.glb",
        mime="model/gltf-binary",
        use_container_width=True,
    )

    # 변환 섹션 (OBJ / PLY)
    st.markdown("---")
    st.markdown("### 🔁 다른 포맷으로 변환")
    export_format = st.selectbox("포맷 선택", ["OBJ", "PLY"], index=0)
    if st.button("변환 실행"):
        try:
            # trimesh 로드
            mesh = trimesh.load(io.BytesIO(st.session_state.glb_bytes), file_type="glb")

            buf = io.BytesIO()
            mesh.export(buf, file_type=export_format.lower())
            out_bytes = buf.getvalue()

            st.download_button(
                label=f"{export_format} 다운로드",
                data=out_bytes,
                file_name=f"model.{export_format.lower()}",
                mime="application/octet-stream",
                use_container_width=True,
            )
            st.success(f"{export_format} 변환 완료!")
        except Exception as e:
            st.error(f"{export_format} 변환 실패: {e}")

# ----------------------------
# 업로드만 되어 있고 아직 시작 안한 경우 미리보기
# ----------------------------
elif uploaded_file is not None and st.session_state.glb_bytes is None:
    preview_left, preview_right = st.columns(2)
    with preview_left:
        st.subheader("원본 이미지")
        st.image(uploaded_file, use_container_width=True)
    with preview_right:
        st.info("오른쪽에는 변환 완료 후 3D 모델이 표시됩니다. '3D 변환 시작'을 눌러주세요.")
