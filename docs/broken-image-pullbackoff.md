# Kubernetes ImagePullBackOff Troubleshooting

## Issue

Kubernetes cannot download the container image configured in a Deployment.

## Error

```text
ErrImagePull
ImagePullBackOff
```

## Scope

This procedure applies to any namespace and any Deployment that cannot pull its
configured container image.

Replace `<namespace>`, `<deployment-name>`, and `<pod-name>` with actual values.

## Symptoms

- Pod status is `ImagePullBackOff` or `ErrImagePull`.
- Pod readiness is `0/1` or `0/<container-count>`.
- The Deployment does not have the expected available replicas.
- The Service may have no ready endpoints.

## Diagnosis

### 1. Find the failing pods

```powershell
kubectl get pods -n <namespace>
```

### 2. Inspect the failing pod

```powershell
kubectl describe pod <pod-name> -n <namespace>
```

Review the `Events` section for messages such as:

```text
Failed to pull image
manifest unknown
ErrImagePull
Back-off pulling image
```

### 3. Check the image configured in the Deployment

```powershell
kubectl get deployment <deployment-name> -n <namespace> -o jsonpath="{.spec.template.spec.containers[*].image}"
```

### 4. Review recent events

```powershell
kubectl get events -n <namespace> --sort-by=.lastTimestamp
```

## Root Cause

The Deployment references an image that Kubernetes cannot pull.

Common causes:

- The image tag does not exist.
- The image name is incorrect.
- The private registry requires authentication.
- The node cannot reach the container registry.
- The image architecture is not supported by the node.

## Fix: Invalid Image Name or Tag

Update the Deployment to use a valid image and tag:

```powershell
kubectl set image deployment/<deployment-name> `
  <container-name>=<registry>/<image>:<valid-tag> `
  -n <namespace>
```

Example:

```powershell
kubectl set image deployment/broken-image-demo `
  nginx=nginx:1.27-alpine `
  -n dev
```

## Fix: Private Registry Authentication

Create an image-pull secret:

```powershell
kubectl create secret docker-registry <secret-name> `
  --docker-server=<registry> `
  --docker-username=<username> `
  --docker-password=<password> `
  -n <namespace>
```

Reference the secret in the Deployment:

```yaml
spec:
  template:
    spec:
      imagePullSecrets:
        - name: <secret-name>
```

## Verification

Check the rollout:

```powershell
kubectl rollout status deployment/<deployment-name> -n <namespace>
```

Check pod readiness:

```powershell
kubectl get pods -n <namespace>
```

Check Deployment availability:

```powershell
kubectl get deployment <deployment-name> -n <namespace>
```

Check Service endpoints:

```powershell
kubectl get endpoints <service-name> -n <namespace>
```

Expected result:

```text
Pod status: Running
Pod readiness: Ready
Deployment: Available replicas match desired replicas
Service: Ready endpoints are present
```

## Rollback

```powershell
kubectl rollout undo deployment/<deployment-name> -n <namespace>
kubectl rollout status deployment/<deployment-name> -n <namespace>
```

## Prevention

- Validate image names and tags before deployment.
- Use immutable image tags or image digests.
- Verify private registry credentials and image-pull secrets.
- Confirm node network access to the registry.
- Monitor Deployment rollout status after image changes.
