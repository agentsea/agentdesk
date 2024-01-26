#!/bin/bash

cat <<EOF | kubectl apply -f -
apiVersion: kubevirt.io/v1
kind: KubeVirt
metadata:
  name: kubevirt
  namespace: kubevirt
spec:
  certificateRotateStrategy: {}
  configuration:
    developerConfiguration:
      featureGates:
        - MultiArchitecture
  customizeComponents: {}
  imagePullPolicy: IfNotPresent
  workloadUpdateStrategy: {}
  infra:
    nodePlacement:
      nodeSelector:
        workload: infra
      tolerations:
        - key: CriticalAddonsOnly
          operator: Exists
EOF