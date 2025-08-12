# AWS Prod (EKS + RDS + Redis + S3 + Secrets Manager)

> Uses community modules for VPC/EKS, private subnets, IRSA enabled.

## Usage
```bash
cd infra/terraform/aws-prod
terraform init
terraform apply -auto-approve   -var 'region=eu-west-1'   -var 'project=agentic'   -var 'db_password=YOUR_STRONG_PASS'
```

Outputs include:
- `cluster_name`, `cluster_endpoint`
- `database_url`, `redis_url`, `redis_url_queue`, `s3_bucket`
- `secret_name` (AWS Secrets Manager path storing app config)

## Next
1. Update your kubecontext to the new cluster (see EKS documentation).
2. Install dependencies:
```bash
helm repo add external-secrets https://charts.external-secrets.io
helm install external-secrets external-secrets/external-secrets -n external-secrets --create-namespace

helm repo add aws-load-balancer-controller https://aws.github.io/eks-charts
helm install aws-lbc aws-load-balancer-controller/aws-load-balancer-controller   -n kube-system   --set clusterName=$(terraform output -raw cluster_name)   --set serviceAccount.create=true   --set region=$(terraform output -raw region)   --set vpcId=$(terraform output -raw vpc_id)
```
3. Deploy Helm chart with `values.yaml` updated to point images to GHCR and enable `externalSecrets`.
