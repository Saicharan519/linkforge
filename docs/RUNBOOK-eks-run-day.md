# Run day: put LinkForge on AWS (EKS) step by step, for a beginner

Read this top to bottom BEFORE you start, once, without typing anything. Then do it one step at a time.
Everything marked **YOU** is typed by you in your own WSL terminal or clicked in the AWS console. Claude never runs anything
against AWS. When something fails, you paste the log file into the chat and Claude reads it.

Nothing in this file was run against a real AWS account when it was written. The commands come from the repo's own scripts and
README files and were checked offline only (fmt, validate, Checkov, 43 script checks with fake tools). **Every "expected output"
below is what should happen, not something that has been seen.** If reality differs, stop and paste the log.

---

## Step 0. What is going to happen

**Goal:** build a small Kubernetes cluster (EKS) in AWS Sydney, deploy LinkForge on it with Argo CD, check that it works, take
numbers and screenshots, then DELETE EVERYTHING the same day.

**What AWS will create** (all with Terraform, from the folder `infra/terraform/cloud`):

| Thing | What it is | Costs money? |
|---|---|---|
| VPC, subnets, internet gateway | your private network in AWS | no |
| NAT gateway (one) | lets private servers reach the internet | **yes, per hour** |
| EKS control plane | the "brain" of Kubernetes | **yes, about $0.10 per hour** |
| 2 or 3 worker nodes | the EC2 servers that run the pods | **yes, per hour** |
| RDS PostgreSQL (db.t4g.micro) | the database | **yes, per hour** |
| 2 load balancers (ALB) | made later by Kubernetes, one per Ingress | **yes, per hour** |
| EBS volume (gp3) | the disk for Redis | yes, small |

**Rough size of the bill:** a few dollars for a whole day. These figures are estimates, not measured. The real prices are on the AWS
pricing pages for ap-southeast-2. You have about $96 of credits (seen on 2026-11-05, they expire 2027-04-28). The danger is not
the first day. The danger is **forgetting to delete it**: an EKS setup left running for a month costs real money.

**Nothing costs money until Step 5, stage 2** (the EKS cluster). Steps 1 to 4 and the VPC stage are free or almost free (the NAT
gateway is the only hourly item in stage 1, and you will delete it quickly if you stop).

**How long:** plan one free afternoon, about 3 to 4 hours, in ONE sitting. Do not start at night and go to sleep with it running.
Creating the cluster alone takes about 15 minutes of waiting.

**What can go wrong (honest list):**
1. Your account may not be allowed to create EKS, a NAT gateway, RDS or a load balancer (it is a new-style Free-plan account and
   this is NOT confirmed). Then Terraform shows `AccessDenied` or "not available on the Free plan". That is not your mistake.
2. The server type may not be allowed. We check before paying.
3. Some assumptions in my design may be wrong (for example, the two Ingresses might share one load balancer). The scripts check
   these and stop with a clear message.

**When to STOP and not continue:**
- any step says `AccessDenied`, `not authorized`, `not available on the Free plan`, `quota`: stop, paste the log in chat;
- you are tired or have to leave: run the teardown (Step 12) first, then leave;
- the bill or credits look wrong in the console.

**The golden rule:** if you are unsure at any moment, do not type the next command. Paste what you see in the chat.

---

## Step 1. Check the AWS account (browser, no commands)

**Why:** make sure the budget alert works before anything costs money.

1. Open https://console.aws.amazon.com and sign in.
2. Top right, check the region name says **Asia Pacific (Sydney)**. If not, click it and choose Sydney. This account can only use Sydney.
3. In the search box at the top type `Billing` and open **Billing and Cost Management**.
4. Find the credits page (left menu: **Credits**). You should see about $96 remaining and an expiry of 2027-04-28. Write the number
   down. After the run day you compare.
5. Left menu: **Budgets**. You should see the budget you created in an earlier phase. Open it and check the alert email address is
   yours.
6. Open your email inbox. Look for the "confirm subscription" email from AWS about the budget. If it is in the spam folder, open
   it and confirm. (This was still unverified.)

**If it looks different:** if there is no budget or no credits page, take a screenshot and paste it into the chat. Do not continue.

---

## Step 2. Give the IAM user `linkforge-terraform` the permissions it needs (browser)

**Why:** right now this user can only touch the state bucket. Terraform needs permission to create the network, EKS, RDS and roles.
That is why you got `UnauthorizedOperation` earlier: expected.

### 2a. Find your 12-digit account id
Top right of the console, click your account name. The 12-digit number is the **Account ID**. Write it down. You will use it in
several places. It is not a password, but we keep it out of git.

### 2b. Make the policy text
1. In WSL, open the policy file to read it:

```bash
nano ~/linkforge/infra/terraform/cloud/iam-policy-p9.json
```

2. This is a copy for you to READ. Do not save changes to the repo file. Press `Ctrl+X` to leave. If it asks to save, press `N`.
3. You need the same text in the AWS console with your account id instead of `<ACCOUNT_ID>`. The easiest way is to make a private copy
   in your home folder and edit that:

```bash
cp ~/linkforge/infra/terraform/cloud/iam-policy-p9.json ~/iam-policy-p9.mine.json
```

4. Replace the placeholder with your account id. Replace `123456789012` below with YOUR number, then run:

```bash
sed -i 's/<ACCOUNT_ID>/123456789012/g' ~/iam-policy-p9.mine.json
```

5. Check that no placeholder is left. Expected output: **nothing at all**:

```bash
grep -c '<ACCOUNT_ID>' ~/iam-policy-p9.mine.json
```

Expected: the number `0`. If you see another number, repeat step 4.

6. Show the file so you can copy it:

```bash
cat ~/iam-policy-p9.mine.json
```

Select the whole text with the mouse and copy it (right click or Ctrl+Shift+C in the terminal).

### 2c. Put it in the console
1. Console search box: type `IAM`, open **IAM**.
2. Left menu: **Users**. Click **linkforge-terraform**.
3. Tab **Permissions**. You will see the existing policy (the S3 state one). Leave it.
4. Click **Add permissions**, then **Create inline policy**.
5. Click the **JSON** button (not "Visual").
6. Delete everything in the box. Paste the copied text.
7. Click **Next**. Name it `linkforge-p9`. Click **Create policy**.

(If the existing S3 policy has a similar permission, that is fine, the new policy contains the state bucket permission too.)

### 2d. A second small policy for the read-only checks of Step 3
The big policy does not include the quota service. Do the same clicks again (**Add permissions, Create inline policy, JSON**) with
this text, name it `linkforge-preflight`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["servicequotas:GetServiceQuota", "servicequotas:ListServiceQuotas", "sts:GetCallerIdentity"],
      "Resource": "*"
    }
  ]
}
```

**Expected:** the Permissions tab now lists three policies.

**If it looks different:** an error "policy contains invalid JSON" means the paste lost a character. Paste it again. An error that mentions
an explicit deny: take a screenshot and paste it in the chat.

**Important:** the account's own AWS-managed policy may still deny things. An explicit deny always wins. Only the first Terraform run
shows this. That is why we apply in stages.

---

## Step 3. Read-only checks (free, they create nothing)

**Why:** find out which server types are allowed and how many pods fit, BEFORE paying.

### 3a. Make a log folder and choose the profile

```bash
mkdir -p ~/runlogs
```

Check that your AWS profile exists. Expected: a list that includes `linkforge`:

```bash
aws configure list-profiles
```

If `aws: command not found`, stop and tell Claude. If your profile has another name, use that name wherever this guide says `linkforge`.

Use the profile only in this terminal window (it disappears when you close the window, which is what we want):

```bash
export AWS_PROFILE=linkforge
```

### 3b. Check that you are the right user

```bash
aws sts get-caller-identity
```

Expected: three lines with `UserId`, `Account` (your 12 digits) and `Arn` ending in `user/linkforge-terraform`. If you see
`InvalidClientTokenId` or `ExpiredToken`, the key in `~/.aws` is wrong or inactive. Stop and tell Claude (do not paste the keys).

### 3c. Is each server type allowed in Sydney?

```bash
aws ec2 describe-instance-type-offerings --region ap-southeast-2 --filters Name=instance-type,Values=t3.medium,m7i-flex.large --query 'InstanceTypeOfferings[].InstanceType' 2>&1 | tee ~/runlogs/preflight-offerings.log
```

Expected: a list like `["t3.medium","m7i-flex.large"]`. This only says AWS sells those types in Sydney. It does NOT prove your Free-plan
account may launch them. That is only known when the node group is created.

### 3d. Network limits and memory

```bash
aws ec2 describe-instance-types --region ap-southeast-2 --instance-types t3.medium m7i-flex.large --query 'InstanceTypes[].[InstanceType,VCpuInfo.DefaultVCpus,MemoryInfo.SizeInMiB,NetworkInfo.MaximumNetworkInterfaces,NetworkInfo.Ipv4AddressesPerInterface]' --output text 2>&1 | tee ~/runlogs/preflight-types.log
```

Expected: two lines with 5 values each (name, vCPUs, memory MiB, number of network cards, addresses per card). How to read it:
**pods per node = cards x (addresses minus 1) + 2**. The repo already computed t3.medium as 17 pods per node. About 4 slots per node go to system
pods. LinkForge needs about 24 pods at rest and about 29 at its busiest.

### 3e. vCPU quota

```bash
aws service-quotas get-service-quota --region ap-southeast-2 --service-code ec2 --quota-code L-1216C47A --query 'Quota.Value' 2>&1 | tee ~/runlogs/preflight-quota.log
```

Expected: a number such as `5.0` or `32.0`. You need at least (number of nodes x vCPUs of the type). Two m7i-flex.large need 4. Three
t3.medium need 6.

### 3f. Send it to Claude
Paste the content of the three files into the chat:

```bash
cat ~/runlogs/preflight-offerings.log ~/runlogs/preflight-types.log ~/runlogs/preflight-quota.log
```

**Claude then tells you which server route to use** (default: 2 x m7i-flex.large, alternative: 3 x t3.medium). Do not choose before this.

**If it looks different:** `UnauthorizedOperation` or `AccessDenied` on one of them: paste the exact message. It names the missing action.

---

## Step 4. Prepare the two settings files (no AWS cost)

**Why:** Terraform needs your account id and your IP address. These files are git-ignored (they never get committed).

### 4a. Find your public IP

```bash
curl -s https://checkip.amazonaws.com
```

Expected: one line such as `203.0.113.7`. That is the address of your internet connection. Write it down. If your internet provider
changes it during the day (restarting the router can do it), you lose access to the cluster API and the load balancers. See "My IP changed" at the end.

### 4b. Create `terraform.tfvars`

```bash
cd ~/linkforge/infra/terraform/cloud
```

```bash
cp terraform.tfvars.example terraform.tfvars
```

```bash
nano terraform.tfvars
```

In nano, use the arrow keys. Change exactly these two lines:
- `allowed_account_id = "123456789012"`: put YOUR 12 digits between the quotes;
- `admin_cidrs = ["203.0.113.7/32"]`: put YOUR IP and keep `/32` at the end.

After Claude has given you the server route, remove the `#` at the start of the matching lines. Example for the default route, the
lines must look like this (no `#`):

```
node_instance_types = ["m7i-flex.large"]
node_min_size       = 2
node_desired_size   = 2
node_max_size       = 3
```

Leave `domain_name` commented out (HTTP mode). To save: `Ctrl+O`, then `Enter`. To leave: `Ctrl+X`.

### 4c. Create `backend.hcl`

```bash
cp ../backend.hcl.example backend.hcl
```

```bash
nano backend.hcl
```

Change `linkforge-tfstate-<ACCOUNT_ID>` to `linkforge-tfstate-` followed by your 12 digits. Save and leave as before.

### 4d. Check

```bash
grep -n 'ACCOUNT_ID\|123456789012\|203.0.113.7' terraform.tfvars backend.hcl
```

Expected: **no output**. Any line shown is an example value you forgot to replace.

---

## Step 5. Build it with Terraform, in three stages

**Why in stages:** the first stage is cheap. If your account refuses something, we learn it early, and cheaply.

Open the terminal where you typed `export AWS_PROFILE=linkforge`. If you closed it, type it again. Then:

```bash
cd ~/linkforge/infra/terraform/cloud
```

### 5a. Initialise (downloads the plugins, connects to the state bucket)

```bash
terraform init -backend-config=backend.hcl 2>&1 | tee ~/runlogs/cloud-init.log
```

Expected: ends with `Terraform has been successfully initialized!`. If you see `AccessDenied` for the S3 bucket or `NoSuchBucket`:
check the bucket name in `backend.hcl`, then paste the log.

### 5b. Plan (shows what WOULD be created; creates nothing)

```bash
terraform plan -out=tfplan 2>&1 | tee ~/runlogs/cloud-plan.log
```

Expected: a long list, ending with a line like `Plan: NN to add, 0 to change, 0 to destroy.` with NN around 70 to 100 (a guess).
Nothing to change or destroy on a first run. **Paste the last 40 lines to Claude before continuing:**

```bash
tail -40 ~/runlogs/cloud-plan.log
```

Never paste the file `tfplan` itself (it contains the database password).

### 5c. Stage 1: the network (cheap)

```bash
terraform apply -target=module.vpc 2>&1 | tee ~/runlogs/cloud-apply-vpc.log
```

Terraform shows a plan and asks `Enter a value:`. Type `yes` and press Enter. Warning lines about `-target` are normal.
Expected: about 3 to 6 minutes (the NAT gateway is the slow part), ending with `Apply complete! Resources: ... added`.

**From here the NAT gateway costs money per hour.** If this fails with `AccessDenied`: run `terraform destroy` (it deletes what exists,
type `yes`) and paste `~/runlogs/cloud-apply-vpc.log`.

### 5d. Stage 2: EKS (this is where the big costs start)

```bash
terraform apply -target=module.eks 2>&1 | tee ~/runlogs/cloud-apply-eks.log
```

Type `yes`. Expected: **15 to 25 minutes** of waiting. Lines like `Still creating... [10m0s elapsed]` are normal. It ends with `Apply complete!`.
Do not press Ctrl+C. If it fails, do not retry blindly: paste the log. The most likely failures are an `AccessDenied` (permission),
`not eligible for Free Tier` (server type), or `InvalidParameterException` about the Kubernetes version.

### 5e. Stage 3: everything else (database, roles)

```bash
terraform apply 2>&1 | tee ~/runlogs/cloud-apply-rest.log
```

Type `yes`. Expected: 5 to 15 minutes, mostly the database. It ends with `Apply complete! Resources: ... added` and a list of outputs. If it asks
for a variable value (like `Enter a value:` for something unknown), stop with Ctrl+C and tell Claude.

**Success test for Step 5:** run this. Expected: `No changes. Your infrastructure matches the configuration.`

```bash
terraform plan 2>&1 | tail -5
```

---

## Step 6. Connect kubectl to the new cluster, in its own file

**Why:** a separate file makes sure you can never mix this cluster with the kind one by accident.

```bash
terraform output -raw kubeconfig_command | bash
```

Expected: a line like `Added new context ... to /home/<you>/.kube/linkforge-eks.yaml`. (This runs an `aws eks update-kubeconfig` command that
writes only the separate file.)

```bash
export KUBECONFIG=~/.kube/linkforge-eks.yaml
```

Do this ONLY in the terminal where you work on EKS. Close this terminal at the end. Test:

```bash
kubectl get nodes
```

Expected: 2 (or 3) lines with `STATUS Ready`. If it hangs or says `Unable to connect`, your IP is probably not in `admin_cidrs`: compare with
`curl -s https://checkip.amazonaws.com`. If `Ready` does not appear for 5 minutes, paste the output of `kubectl get nodes` and `kubectl get pods -A`.

---

## Step 7. Install Argo CD and the platform pieces

Same terminal (with `KUBECONFIG` set):

```bash
cd ~/linkforge
```

```bash
scripts/bootstrap-eks.sh 2>&1 | tee ~/runlogs/bootstrap-eks.log
```

Expected: five numbered steps. Step 5 prints a line every 15 seconds like
`sealed-secrets=Synced/Healthy eks-storage=... metrics-server=... aws-load-balancer-controller=...`. It takes about 5 to 15 minutes. It ends with
`Done. Platform is up`.

If it ends with `NOT all platform apps are Healthy`: paste `~/runlogs/bootstrap-eks.log`, and also:

```bash
kubectl -n kube-system logs deploy/aws-load-balancer-controller --tail=40
```

(The likely cause is the load balancer controller not finding the region or VPC. The design assumed it finds them on its own; this is unconfirmed.)

---

## Step 8. Put your IP into the app settings (gitops repo)

**Why:** the two load balancers must only accept YOUR IP, because there is no HTTPS.

```bash
cd ~/linkforge-gitops
```

```bash
git status --short
```

Expected: no output (clean). If something is listed, stop and tell Claude.

Put your IP in (this takes your current IP automatically):

```bash
sed -i "s|REPLACE_WITH_YOUR_IP/32|$(curl -s https://checkip.amazonaws.com)/32|" envs/eks/values.yaml
```

```bash
git diff
```

Expected: exactly one changed line, `inbound-cidrs: <your ip>/32`. If anything else changed, stop.

```bash
git commit -am "chore(eks): allow my IP on the load balancers"
```

```bash
git push origin main
```

---

## Step 9. Seal the secrets and switch the app on

**Why:** the database password and a secret key are encrypted so they can be stored in git safely. Only this cluster can decrypt them.

```bash
cd ~/linkforge
```

```bash
scripts/eks-seal-and-enable.sh 2>&1 | tee ~/runlogs/eks-seal-and-enable.log
```

Expected: four numbered steps; it prints the database user and host, the PASSWORD LENGTH only (never the password), and the length of the
ciphertexts, then `pushed.`. If it says `REPLACE_ placeholder`, Step 8 was skipped. If it says `terraform output returned nothing`, check
`echo $AWS_PROFILE` in this terminal (it must print `linkforge`).

Watch the app come up (stop watching with `Ctrl+C`):

```bash
kubectl -n argocd get applications -w
```

Expected within about 5 to 10 minutes: `linkforge` appears and becomes `Synced Healthy`. Pods:

```bash
kubectl -n linkforge get pods
```

Expected: pods `Running` (the migration pod shows `Completed`). If something stays `Pending`, run `kubectl -n linkforge describe pod <name>` and paste the end:
`Too many pods` would mean the node route was too small.

---

## Step 10. Wait for the two load balancers, then finish the setup

```bash
kubectl -n linkforge get ingress
```

Run it every minute. Expected after 2 to 5 minutes: two rows, `app` and `go`, each with an ADDRESS like `k8s-linkforge-xxxx-1234567890.ap-southeast-2.elb.amazonaws.com`.
**The two addresses must be different.** (A new ALB needs another 1 to 2 minutes before it answers.)

```bash
scripts/eks-set-hosts.sh 2>&1 | tee ~/runlogs/eks-set-hosts.log
```

Expected: `two different load balancers:` with the names, then `pushed. The pods roll once.`. If it says BOTH Ingresses share ONE load balancer, stop:
the design must change, paste the log, nothing was altered.

Wait about 3 minutes for the pods to restart, then:

```bash
scripts/eks-verify.sh 2>&1 | tee ~/runlogs/eks-verify.log
```

Expected: PASS lines and at the end `ALL CHECKS PASSED`. If any line says FAIL, paste `~/runlogs/eks-verify.log`.

---

## Step 11. Use the app, then the measurements

1. Get the two addresses:

```bash
kubectl -n linkforge get ingress
```

2. In your browser open `http://<the app address>` (http, NOT https; the browser may say "Not secure": expected, there is no TLS).
3. **Use a fake test account** (a made-up email and a password you use nowhere else). Passwords cross the internet in clear text.
4. Create a short link. The UI shows it as `http://<the go address>/<code>`. Open it in another tab: it must redirect. Open the analytics page: the click must appear.
5. Take screenshots (dashboard, the short link, the analytics page).

After this works, tell Claude "Step 11 works". Then comes P10: k6 load test on AWS, the failure drills, the screenshots and `RESULTS.md`. Claude will give a
detailed guide for it then. Keep the cluster running only as long as you need it.

---

## Step 12. Delete everything (do not skip, do not postpone)

**Why:** the cluster bills every hour. The order matters: Kubernetes created the load balancers and the Redis disk, so Kubernetes must delete them first.

In the terminal with `KUBECONFIG` set:

```bash
cd ~/linkforge
```

```bash
scripts/eks-teardown.sh 2>&1 | tee ~/runlogs/eks-teardown.log
```

It asks you to type the first part of the cluster's host name (it is printed). Type exactly that. Expected: it ends with `The cluster is clean.` If it prints
`STILL THERE`, do NOT continue: paste the log.

Then Terraform:

```bash
cd ~/linkforge/infra/terraform/cloud
```

```bash
terraform destroy 2>&1 | tee ~/runlogs/cloud-destroy.log
```

Type `yes`. Expected: 15 to 25 minutes, ends with `Destroy complete! Resources: ... destroyed.` If it fails (often a security group or network interface
that cannot be deleted), paste the log. Do not give up and leave: leftovers keep billing.

### Console check (browser, Sydney region selected)
Open each service and check the list is empty of linkforge items (your own `orderintel` EC2 instance is NOT part of this and stays):
1. **EKS**: Clusters (empty).
2. **EC2**: Instances; Volumes; Snapshots (Owned by me); Load Balancers; Target Groups; Elastic IPs; NAT gateways (VPC service); Network interfaces.
3. **VPC**: Your VPCs (no `linkforge` VPC).
4. **RDS**: Databases and Snapshots (manual snapshots are not deleted by Terraform).
Anything left with the tag `Project = linkforge`: delete it by hand and tell Claude.

### Credentials and costs
1. IAM, Users, `linkforge-terraform`, tab **Security credentials**: **Deactivate** the access key (or delete it) when all AWS work is finished. Remove the
   `linkforge-p9` and `linkforge-preflight` inline policies if you will not do another run.
2. Delete your private policy copy:

```bash
rm ~/iam-policy-p9.mine.json
```

3. Close the terminal window (this removes `AWS_PROFILE` and `KUBECONFIG`).
4. **Next day:** Billing, **Bills** and **Credits**. Compare with the number from Step 1. Costs appear with a delay of up to a day. Paste the figures in the chat and
   Claude records them in the results.

---

## Appendix: common problems

- **My IP changed** (`kubectl` hangs, the site does not open): get the new IP, edit `admin_cidrs` in `terraform.tfvars` and run `terraform apply`; edit `inbound-cidrs` in
  `~/linkforge-gitops/envs/eks/values.yaml`, commit and push.
- **I closed the terminal in the middle**: open a new one and type again `export AWS_PROFILE=linkforge` and `export KUBECONFIG=~/.kube/linkforge-eks.yaml`, then continue
  with the step you were on. Terraform remembers what exists (the state is in the S3 bucket).
- **Terraform says the state is locked**: another run may still be going, or one was interrupted. Paste the message; do not force-unlock yourself.
- **Any doubt**: run Step 12 and start another day.
