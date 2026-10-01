# 엔진 + 화면 = 어플리케이션 한 이미지.
# 화면은 gentleMonster 저장소(gentle_monster/apps/worldtrip)에서 빌드할 때 받아 온다 -- 복사본을 이 저장소에 두지 않는다.
#   docker build --build-arg FRONTEND_REF=main -t worldtrip .
FROM python:3.12-slim
ARG FRONTEND_REF=main
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    WORLDTRIP_HOST=0.0.0.0 WORLDTRIP_PORT=8766 WORLDTRIP_LEDGER_ROOT=/ledger \
    WORLDTRIP_FRONTEND=/app/frontend
WORKDIR /app
COPY pyproject.toml README.md ./
COPY worldtrip ./worldtrip
RUN pip install --no-cache-dir . \
 && XDG_CACHE_HOME=/tmp/fe python -c "from worldtrip import frontend; import shutil; shutil.copytree(frontend.fetch('${FRONTEND_REF}'), '/app/frontend')" \
 && rm -rf /tmp/fe \
 && useradd --system --uid 10001 app && mkdir -p /ledger && chown app /ledger
USER app
VOLUME ["/ledger"]
EXPOSE 8766
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ['WORLDTRIP_PORT']+'/healthz',timeout=4)"
# 밖으로 열 때는 -e WORLDTRIP_TOKEN=... 를 세워라
CMD ["worldtrip", "serve"]
