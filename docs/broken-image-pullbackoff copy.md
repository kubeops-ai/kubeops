# Kubernetes ImagePullBackOff

## Problem

Kubernetes cannot download the container image configured in a workload. The
container cannot start until the image is available on the node.

## Symptoms

- Pod status is `ErrImagePull` or `ImagePullBackOff`.
- Pod readiness is `0/1`.
- The Deployment has fewer available replicas than desired.
- The container has no application logs because it never started.

Example:

```text
NAME          READY   STATUS             RESTARTS
<pod-name>    0/1     ImagePullBackOff   0
```

## Diagnosis

List pods in the workload namespace:

```powershell
kubectl get pods -n <namespace>
```

Describe the failing pod:

```powershell
kubectl describe pod <pod-name> -n <namespace>
```

Review the `Events` section. The error usually contains:

```text
Failed to pull image
manifest unknown
ErrImagePull
Back-off pulling image
```

Check the image configured in the Deployment:

```powershell
kubectl get deployment <deployment-name> -n <namespace> -o jsonpath="{.spec.template.spec.containers[*].image}"
```

## Root Cause

The Deployment references an image name or tag that does not exist in the
container registry.

Example of an invalid image:

```text
nginx:1.27-kubeops-does-not-exist
```

## Fix

Update the Deployment to use a valid image tag:

```powershell
kubectl set image deployment/<deployment-name> <container-name>=nginx:1.27-alpine -n <namespace>
```

Example:

```powershell
kubectl set image deployment/broken-image-demo nginx=nginx:1.27-alpine -n dev
```

If the image is defined in a YAML file, update the `image` value and apply the
manifest again:

```powershell
kubectl apply -f <corrected-manifest.yaml>
```

## Verification

Watch the Deployment rollout:

```powershell
kubectl rollout status deployment/<deployment-name> -n <namespace>
```

Check the pod:

```powershell
kubectl get pods -n <namespace>
```

Expected result:

```text
READY   STATUS    RESTARTS
1/1     Running   0
```

The pod should no longer show `ErrImagePull` or `ImagePullBackOff`.