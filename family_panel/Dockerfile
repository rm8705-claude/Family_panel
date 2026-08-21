# Family Panel — Home Assistant add-on image.
#
# BUILD CONTEXT NOTE: the Supervisor builds this image using THIS FOLDER
# (deploy/addon/family_panel/) as the Docker build context — it cannot see
# anything outside it. The Family Panel Python app lives at the family-panel
# repo root, one level above deploy/, which Docker can't reach from here.
#
# That's what deploy/make_addon.sh is for: run it from the repo root and it
# copies the app source (app.py, config.py, db.py, jobs.py, ha.py, weather.py,
# magic_import.py once they exist, requirements.txt, static/, config.yaml)
# into ./app right here, next to this Dockerfile. That app/ folder is what
# COPY below picks up. See deploy/make_addon.sh and deploy/SETUP.md.
#
# If ./app is missing or stale, run (from the repo root):
#   bash deploy/make_addon.sh

ARG BUILD_FROM
FROM $BUILD_FROM

ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="addon" \
    io.hass.arch="${BUILD_ARCH}"

RUN apk add --no-cache python3 py3-pip

# Build tools for any dependency that doesn't ship a prebuilt musllinux
# (Alpine) wheel for this architecture and falls back to compiling from
# source (pdfplumber's Pillow/pypdfium2, or pydantic-core via anthropic, are
# the likely culprits). Small cost, avoids an opaque pip failure on first
# install — remove once you've confirmed every dependency has a musllinux
# wheel for both aarch64 and amd64.
RUN apk add --no-cache gcc musl-dev python3-dev libffi-dev jpeg-dev zlib-dev

WORKDIR /app

# Dependencies first so Docker can cache this layer across rebuilds.
COPY app/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --break-system-packages -r /app/requirements.txt

COPY app/ /app/

COPY run.sh /run.sh
RUN chmod a+x /run.sh

EXPOSE 8080

CMD [ "/run.sh" ]
