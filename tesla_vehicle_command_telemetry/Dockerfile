ARG TELEMETRY_IMAGE=ghcr.io/expansing/fleet-telemetry:latest

FROM ${TELEMETRY_IMAGE} AS upstream

FROM debian:bookworm-slim

COPY --from=upstream /fleet-telemetry /fleet-telemetry
COPY run.sh /run.sh

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        ca-certificates \
        tzdata \
		jq \
		librdkafka1 \
    	libzmq5 \
    	libsodium23 \
    	libpgm-5.3-0 \
    	libnorm1 \
    	libbsd0 \
    	libmd0 \
    	libkrb5-3 \
    	libgssapi-krb5-2 \
    && rm -rf /var/lib/apt/lists/* \
    && chmod 755 /run.sh

ENTRYPOINT ["/run.sh"]
