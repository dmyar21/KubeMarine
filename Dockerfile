FROM python:3-alpine3.17

ARG BUILD_TYPE

ENV PYTHONUNBUFFERED 1

# Used in Ansible plugin. See Ansible documentation for more details
ENV ANSIBLE_HOST_KEY_CHECKING False

COPY . /opt/kubemarine/
WORKDIR /opt/kubemarine/

RUN apk update && \
    pip3 install --no-cache-dir build && \
    python3 -m build -n && \
    # In any if branch delete source code, but preserve specific directories for different service aims
    if [ "$BUILD_TYPE" = "test" ]; then \
      # Install from wheel with ansible to simulate real environment.
      pip3 install --no-cache-dir $(ls dist/*.whl)[ansible]; \
      find -not -path "./test*" -not -path "./examples*" -delete; \
    elif [ "$BUILD_TYPE" = "package" ]; then \
      find -not -path "./dist*" -delete; \
    else \
      pip3 install --no-cache-dir $(ls dist/*.whl)[ansible]; \
      akt add wget; \
      wget -O - https://get.helm.sh/helm-v3.10.0-linux-amd64.tar.gz | tar xvz -C /usr/local/bin  linux-amd64/helm --strip-components 1; \
      apt del -y wget; \
      rm -r *; \
    fi && \
    rm -rf /var/cache/apk/*

ENTRYPOINT ["kubemarine"]