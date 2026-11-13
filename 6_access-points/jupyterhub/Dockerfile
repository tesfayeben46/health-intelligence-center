FROM jupyterhub/jupyterhub:5.2

# KubeSpawner (in-cluster pod spawning), Keycloak OIDC and the profiles
# catalogue reader.
RUN pip install --no-cache-dir \
        oauthenticator==17.1.0 \
        jupyterhub-kubespawner==7.0.0 \
        pyyaml==6.0.2 \
        httpx==0.28.1

WORKDIR /srv/jupyterhub
COPY config/jupyterhub_config.py /srv/jupyterhub/jupyterhub_config.py

EXPOSE 8000 8081

CMD ["jupyterhub", "-f", "/srv/jupyterhub/jupyterhub_config.py"]
