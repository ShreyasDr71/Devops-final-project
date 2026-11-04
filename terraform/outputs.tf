output "server_ip" {
  value = aws_eip.cardea.public_ip
}

output "ssh" {
  value = "ssh -i ${var.private_key_path} ubuntu@${aws_eip.cardea.public_ip}"
}

output "urls" {
  value = {
    jenkins    = "http://${aws_eip.cardea.public_ip}:8080"
    sonarqube  = "http://${aws_eip.cardea.public_ip}:9000"
    grafana    = "http://${aws_eip.cardea.public_ip}:3000"
    prometheus = "http://${aws_eip.cardea.public_ip}:9090"
    device_a   = "http://${aws_eip.cardea.public_ip}:5001"
    device_b   = "http://${aws_eip.cardea.public_ip}:5002"
  }
}
