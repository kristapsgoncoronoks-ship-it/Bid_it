# Rebuild the unchanged gosu 1.19 release with a supported patched Go compiler.
# The official PG16 base currently embeds Go1.24.6 in its privilege-drop helper.
# No PostgreSQL binary, init script, auth default, UID/GID or PGDATA is changed.
# Upstream source: https://github.com/tianon/gosu/tree/6456aaa0f3c854d199d0f037f068eb97515b7513
# gosu: Apache-2.0; unchanged module checksums/dependencies are verified by Go.
# Match upstream's release-metadata process with the pinned fake-git helper.
# Release 1.19 is normalized to Go semver v1.19.0, matching upstream revision metadata.
# The exact source SHA is pinned above; the source commit timestamp is stamped below.
FROM golang:1.27.3-alpine3.24@sha256:f2c872bcee949b7be9c490efd02b48a8a2fbf947cdf533f1c58525ec9d8170fd AS build
RUN --mount=type=secret,id=build_ca,target=/etc/ssl/certs/ca-certificates.crt,mode=0444 \
    set -eu; \
    apk add --no-cache curl bash; \
    curl --fail --location --proto =https --tlsv1.2 --output /tmp/gosu.tar.gz https://codeload.github.com/tianon/gosu/tar.gz/6456aaa0f3c854d199d0f037f068eb97515b7513; \
    echo '33d7537d588ea49458b9509bcf4554bdf5ceacc66da71e5caa1058ea3b689c3b  /tmp/gosu.tar.gz' | sha256sum -c -; \
    mkdir /src; tar -xf /tmp/gosu.tar.gz -C /src --strip-components=1; \
    curl --fail --location --proto =https --tlsv1.2 --output /tmp/fake-git.tar.gz https://codeload.github.com/tianon/fake-git/tar.gz/aaa875961611764a86a21cf80c250929a7786179; \
    echo '4eebe35bfc2842398d325c09167faa597cd4c29f7049ae239b13822d20dfbf88  /tmp/fake-git.tar.gz' | sha256sum -c -; \
    mkdir /opt/fake-git; tar -xf /tmp/fake-git.tar.gz -C /opt/fake-git --strip-components=1; \
    install -m 0755 /opt/fake-git/fake-git.sh /usr/local/bin/git; \
    mkdir /src/.git; \
    export FAKEGIT_GO_SEMVER=v1.19.0 \
        FAKEGIT_GO_REVISION=1.19 \
        FAKEGIT_GO_TIMESTAMP=1758654578; \
    cd /src; CGO_ENABLED=0 GOTOOLCHAIN=local go build -mod=readonly -trimpath -ldflags='-d -w' -buildvcs=true -o /out/gosu .; \
    go version -m /out/gosu | awk '$1 == "mod" && $2 == "github.com/tianon/gosu" && $3 == "v1.19.0" { found = 1 } END { exit !found }'; \
    /out/gosu --version; /out/gosu nobody id; go version -m /out/gosu; \
    mkdir -p /out/licenses; \
    cp /src/LICENSE /out/licenses/gosu-APACHE-2.0; \
    cp /opt/fake-git/LICENSE /out/licenses/fake-git-APACHE-2.0; \
    cp /usr/local/go/LICENSE /out/licenses/Go-BSD-3-Clause; \
    cp /go/pkg/mod/github.com/moby/sys/user@v0.1.0/LICENSE /out/licenses/moby-sys-user-APACHE-2.0; \
    cp /go/pkg/mod/golang.org/x/sys@v0.1.0/LICENSE /out/licenses/golang-x-sys-BSD-3-Clause; \
    rm /tmp/gosu.tar.gz /tmp/fake-git.tar.gz

# Inherit ENTRYPOINT/CMD, SIGINT, port5432, UID/GID70 and the PG16 data volume.
FROM postgres:16-alpine@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea AS runtime
COPY --from=build --chmod=0755 /out/gosu /usr/local/bin/gosu
COPY --from=build /out/licenses/ /usr/share/licenses/gosu/
RUN gosu --version && gosu postgres id && su-exec nobody true && postgres --version
