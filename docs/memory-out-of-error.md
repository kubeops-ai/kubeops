# Kubernetes Memory Out-of-Memory Troubleshooting

## Issue

A container uses more memory than its configured limit and Kubernetes stops it.

## Error

```text
OOMKilled
```

## Scope

This procedure applies to any namespace and any workload with a container that
is terminated because of memory pressure.

Replace `<namespace>`, `<pod-name>`, `<deployment-name>`, and
`<container-name>` with actual values.

## Symptoms

- Pod status shows `OOMKilled` or `CrashLoopBackOff`.
- Container restart count keeps increasing.
- The application becomes unavailable or restarts unexpectedly.
- The previous container state has exit code `137`.

## Diagnosis

### 1. Check pod status

```powershell
kubectl get pod <pod-name> -n <namespace>
```

### 2. Inspect container termination details

```powershell
kubectl describe pod <pod-name> -n <namespace>
```

Look for:

```text
Reason: OOMKilled
Exit Code: 137
```

### 3. Check current resource usage

```powershell
kubectl top pod <pod-name> -n <namespace> --containers
```

If metrics are unavailable, install or repair Metrics Server before using
`kubectl top`.

### 4. Check configured memory values

```powershell
kubectl get deployment <deployment-name> -n <namespace> -o yaml
```

Review the container `resources.requests.memory` and `resources.limits.memory`.

## Root Cause

The container exceeded its memory limit. Kubernetes terminated the container to
protect the node and other workloads.

Common causes:

- The memory limit is too low.
- The application has a memory leak.
- A request or batch operation uses more memory than expected.
- Too many workers or concurrent requests are running.

## Fix

### Increase the memory limit

```powershell
kubectl set resources deployment/<deployment-name> `
  -n <namespace> `
  --containers=<container-name> `
  --requests=memory=256Mi `
  --limits=memory=512Mi
```

Use values appropriate for the application. Do not increase limits without
checking available node capacity.

### Restart after updating resources

```powershell
kubectl rollout status deployment/<deployment-name> -n <namespace>
```

## Verification

```powershell
kubectl get pods -n <namespace>
kubectl describe pod <pod-name> -n <namespace>
kubectl top pod <pod-name> -n <namespace> --containers
```

The pod should remain `Running`, and the restart count should stop increasing.

## Prevention

- Set realistic memory requests and limits.
- Monitor memory usage over time.
- Test large requests and peak traffic.
- Investigate memory leaks in the application.
- Use autoscaling when workload demand changes.
