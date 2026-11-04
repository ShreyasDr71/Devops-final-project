terraform {
  required_providers {
    aws   = { source = "hashicorp/aws", version = "~> 5.0" }
    local = { source = "hashicorp/local", version = "~> 2.0" }
  }
}

provider "aws" {
  region = var.region
}

data "aws_caller_identity" "me" {}

# Latest Ubuntu 24.04 image
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
}

# ---------- Network: which ports are open ----------

resource "aws_security_group" "cardea" {
  name        = "cardea-sg"
  description = "Cardea app and DevOps tools"

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.my_ip]
  }

  dynamic "ingress" {
    # 5000 hub, 5001-5002 demo devices, 8080 Jenkins, 9000 SonarQube, 9090 Prometheus, 3000 Grafana
    for_each = { hub = [5000, 5002], jenkins = [8080, 8080], sonarqube = [9000, 9000], prometheus = [9090, 9090], grafana = [3000, 3000] }
    content {
      description = ingress.key
      from_port   = ingress.value[0]
      to_port     = ingress.value[1]
      protocol    = "tcp"
      cidr_blocks = ["0.0.0.0/0"]
    }
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ---------- IAM: let the server write logs to CloudWatch ----------

resource "aws_iam_role" "cardea" {
  name = "cardea-ec2-role"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "ec2.amazonaws.com" } }]
  })
}

resource "aws_iam_role_policy" "logs" {
  name = "cardea-write-logs"
  role = aws_iam_role.cardea.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
      Resource = "${aws_cloudwatch_log_group.hub.arn}:*"
    }]
  })
}

resource "aws_iam_instance_profile" "cardea" {
  name = "cardea-ec2-profile"
  role = aws_iam_role.cardea.name
}

# ---------- The server ----------

resource "aws_instance" "cardea" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  key_name               = var.key_name
  vpc_security_group_ids = [aws_security_group.cardea.id]
  iam_instance_profile   = aws_iam_instance_profile.cardea.name

  root_block_device {
    volume_size = 30 # GB, free tier limit
    volume_type = "gp3"
  }

  tags = { Name = "cardea-server" }
}

# Fixed public IP so devices and Jenkins always use the same address
resource "aws_eip" "cardea" {
  instance = aws_instance.cardea.id
  tags     = { Name = "cardea-ip" }
}

# ---------- CloudWatch: logs + CPU alarm ----------

resource "aws_cloudwatch_log_group" "hub" {
  name              = "/cardea/hub"
  retention_in_days = 7
}

resource "aws_cloudwatch_metric_alarm" "cpu" {
  alarm_name          = "cardea-high-cpu"
  namespace           = "AWS/EC2"
  metric_name         = "CPUUtilization"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  threshold           = 80
  comparison_operator = "GreaterThanThreshold"
  dimensions          = { InstanceId = aws_instance.cardea.id }
}

# ---------- CloudTrail: record every AWS API call ----------

resource "aws_s3_bucket" "trail" {
  bucket        = "cardea-trail-${data.aws_caller_identity.me.account_id}"
  force_destroy = true
}

resource "aws_s3_bucket_policy" "trail" {
  bucket = aws_s3_bucket.trail.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "s3:GetBucketAcl"
        Resource  = aws_s3_bucket.trail.arn
      },
      {
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.trail.arn}/AWSLogs/${data.aws_caller_identity.me.account_id}/*"
        Condition = { StringEquals = { "s3:x-amz-acl" = "bucket-owner-full-control" } }
      }
    ]
  })
}

resource "aws_cloudtrail" "cardea" {
  name           = "cardea-trail"
  s3_bucket_name = aws_s3_bucket.trail.id
  depends_on     = [aws_s3_bucket_policy.trail]
}

# ---------- Hand the server over to Ansible ----------

resource "local_file" "inventory" {
  filename = "${path.module}/../ansible/inventory.ini"
  content  = <<-EOT
    [cardea]
    ${aws_eip.cardea.public_ip} ansible_user=ubuntu ansible_ssh_private_key_file=${var.private_key_path}
  EOT
}
