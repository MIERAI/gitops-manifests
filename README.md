# GitOps Declarative Configuration and Deployment

> Parent Project: [Enterprise GitOps Platform Blueprint](../gitops-platform-blueprint)

## Overview
This repository contains the declarative state of all Kubernetes workloads. It is the target source for the ArgoCD operator, representing the "Desired State" of the production and staging environments.

## Repository Structure
- **/charts**: Base Helm Chart templates for standard microservices.
- **/apps**: Environment-specific values (values-dev.yaml, values-prod.yaml) and overlay configurations.

## Delivery Workflow
The deployment is triggered automatically when the image tag is updated in this repository via the CI pipeline of the application repositories.

---
**Component**: GitOps Control Plane  
**Status**: Reference Implementation