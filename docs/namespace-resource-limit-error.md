# Kubernetes Namespace Resource Limit Troubleshooting

## Issue

A workload cannot be created or updated because the namespace has reached its
resource quota or violates its resource limits.

## Error

Common messages include:

```text
exceeded quota
forbidden: failed quota
must specify limits.cpu
must specify limits.memory
```

## Scope

This procedure applies to any namespace controlled by a `ResourceQuota` or
`LimitRange`.

Replace `<namespace>`, `<pod-name>`, and `<resource-name>` with actual values.

## Symptoms

- Deployment or Pod creation is rejected.
- The workload remains unavailable because a new replica cannot be created.
- Kubernetes events show quota or limit validation errors.
- The namespace reports used resources near or at its hard limit.

## Diagnosis

### 1. Check namespace quotas

```powershell
kubectl get resourcequota -n <namespace>
kubectl describe resourcequota -n <namespace>
```

Compare `Used` with `Hard` for CPU, memory, pods, and object counts.

### 2. Check namespace limit ranges

```powershell
kubectl get limitrange -n <namespace>
kubectl describe limitrange -n <namespace>
```

Review required default, minimum, and maximum CPU or memory values.

### 3. Review the rejected workload

```powershell
kubectl describe pod <pod-name> -n <namespace>
kubectl get events -n <namespace> --sort-by=.lastTimestamp
```

## Root Cause

The namespace policy rejected the workload because:

- The quota has no remaining CPU, memory, or pod capacity.
- The workload does not specify required CPU or memory values.
- A requested value is below the `LimitRange` minimum.
- A requested value is above the `LimitRange` maximum.

## Fix: Add Resource Requests and Limits

Add values accepted by the namespace policy:

```yaml
resources:
  requests:
    cpu: 100m
    memory: 128Mi
  limits:
    cpu: 500m
    memory: 512Mi
```

Apply the corrected manifest:

```powershell
kubectl apply -f <corrected-manifest.yaml>
```

## Fix: Quota Has No Remaining Capacity

A namespace administrator must choose one of these actions:

- Reduce resource requests for suitable workloads.
- Remove unused workloads.
- Increase the namespace quota.
- Move the workload to an approved namespace.

Do not delete active workloads without confirming their ownership and impact.

## Verification

```powershell
kubectl get resourcequota -n <namespace>
kubectl get pods -n <namespace>
kubectl get deployment -n <namespace>
kubectl get events -n <namespace> --sort-by=.lastTimestamp
```

The workload should be accepted, scheduled, and available.

## Prevention

- Define CPU and memory requests and limits for every container.
- Monitor quota usage before deployments.
- Keep quota values documented for each environment.
- Use admission validation in CI/CD.
- Alert before quota capacity is exhausted.
