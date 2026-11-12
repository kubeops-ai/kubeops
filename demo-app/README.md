# Three-tier Kubernetes demo

This application has a frontend, FastAPI backend, and PostgreSQL database.

The PostgreSQL database runs inside the cluster as the `demo-db` Deployment and
Service. Its data is stored on the Kubernetes node using host path
`/var/lib/kubeops-demo-postgres`.

Build and push images after replacing `YOUR_DOCKERHUB_USER`:

```powershell
docker build -f .\backend\Dockerfile -t YOUR_DOCKERHUB_USER/kubeops-demo-api:1.0 .\backend
docker build -f .\frontend\Dockerfile -t YOUR_DOCKERHUB_USER/kubeops-demo-frontend:1.0 .\frontend
docker push YOUR_DOCKERHUB_USER/kubeops-demo-api:1.0
docker push YOUR_DOCKERHUB_USER/kubeops-demo-frontend:1.0
```

Update the two image names in `k8s/demo-three-tier.yaml`, then deploy:

```powershell
kubectl apply -f .\k8s\demo-three-tier.yaml
kubectl get pods -n demo-app
kubectl get svc -n demo-app
kubectl describe pod -n demo-app -l app=demo-db
```

The database is intentionally simple for the first deployment. Add a
PersistentVolumeClaim before treating it as production data.
