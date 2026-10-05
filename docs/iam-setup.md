# 🔐 IAM Role Setup — No Static Credentials

This platform uses **IAM Instance Profile** (EC2 IAM Role) instead of
`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`. This is the AWS-recommended
best practice — credentials are automatically rotated every hour and never
stored anywhere on disk.

---

## Architecture

```
EC2 (observability-server)
  └── IAM Role: ObservabilityServerRole
        └── IAM Policy: ObservabilityPlatformPolicy
              └── Permissions:
                    cloudwatch:GetMetricData
                    cloudwatch:ListMetrics
                    ec2:DescribeInstances
                    ecs:ListClusters / DescribeServices
                    lambda:ListFunctions
                    sts:AssumeRole  ← for multi-account
```

---

## Step 1 — Create IAM Policy

1. Go to **AWS Console → IAM → Policies → Create Policy**
2. Select **JSON** tab, paste the policy below
3. Name it: `ObservabilityPlatformPolicy`
4. Click **Create Policy**

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "CloudWatchReadOnly",
      "Effect": "Allow",
      "Action": [
        "cloudwatch:GetMetricData",
        "cloudwatch:GetMetricStatistics",
        "cloudwatch:ListMetrics",
        "cloudwatch:DescribeAlarms",
        "tag:GetResources",
        "tag:GetTagKeys",
        "tag:GetTagValues"
      ],
      "Resource": "*"
    },
    {
      "Sid": "SecretsManagerReadOnly",
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue",
        "secretsmanager:DescribeSecret"
      ],
      "Resource": "arn:aws:secretsmanager:*:496251222247:secret:observability/*"
    },
    {
      "Sid": "SESReadOnly",
      "Effect": "Allow",
      "Action": [
        "ses:SendEmail",
        "ses:SendRawEmail",
        "ses:ListVerifiedEmailAddresses",
        "ses:GetSendStatistics"
      ],
      "Resource": "*"
    },
    {
      "Sid": "EC2ReadOnly",
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeInstances",
        "ec2:DescribeRegions",
        "ec2:DescribeTags"
      ],
      "Resource": "*"
    },
    {
      "Sid": "ECSReadOnly",
      "Effect": "Allow",
      "Action": [
        "ecs:ListClusters",
        "ecs:ListServices",
        "ecs:DescribeServices",
        "ecs:ListTasks",
        "ecs:DescribeTasks"
      ],
      "Resource": "*"
    },
    {
      "Sid": "LambdaReadOnly",
      "Effect": "Allow",
      "Action": [
        "lambda:ListFunctions",
        "lambda:GetFunction"
      ],
      "Resource": "*"
    },
    {
      "Sid": "STSForMultiAccount",
      "Effect": "Allow",
      "Action": [
        "sts:AssumeRole",
        "sts:GetCallerIdentity"
      ],
      "Resource": "*"
    }
  ]
}
```

---

## Step 2 — Create IAM Role

1. Go to **IAM → Roles → Create Role**
2. **Trusted entity** → `AWS Service` → `EC2`
3. Attach policy: `ObservabilityPlatformPolicy`
4. Name it: `ObservabilityServerRole`
5. Click **Create Role**

---

## Step 3 — Attach Role to EC2

1. **EC2 → Instances** → select `observability-server`
2. **Actions → Security → Modify IAM Role**
3. Select `ObservabilityServerRole`
4. Click **Update IAM Role**

---

## Step 4 — Verify on the Server

SSH into your EC2 and run:

```bash
# Test credentials are available via instance metadata
curl -sf http://169.254.169.254/latest/meta-data/iam/security-credentials/
# Should print: ObservabilityServerRole

# Test AWS CLI can use the role
aws sts get-caller-identity
# Should print your account ID and role ARN

# Test CloudWatch access
aws cloudwatch list-metrics --namespace AWS/EC2 --region us-east-1 | head -20
```

---

## Multi-Account Monitoring

To monitor resources in **other AWS accounts**, create a cross-account role
in each target account:

### In each target account — create this role:

**Role name:** `ObservabilityReadOnlyRole`
**Trusted entity (trust policy):**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::<OBSERVABILITY_ACCOUNT_ID>:role/ObservabilityServerRole"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```
Attach the same `ObservabilityPlatformPolicy` to this role.

### In `.env` on the observability server:
```bash
# Add the role ARNs to assume, comma-separated
AWS_ROLE_ARN=arn:aws:iam::111122223333:role/ObservabilityReadOnlyRole,arn:aws:iam::444455556666:role/ObservabilityReadOnlyRole
```

---

## Security Best Practices

| ✅ Do | ❌ Don't |
|-------|---------|
| Use IAM Instance Profile | Store keys in `.env` |
| Use least-privilege policy | Use `AdministratorAccess` |
| Rotate roles, not keys | Hardcode keys in code |
| Use cross-account roles for multi-account | Share one account's keys |
| Enable CloudTrail to audit API calls | Leave CloudTrail disabled |
