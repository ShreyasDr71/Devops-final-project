output "server_ip" {
  value = aws_eip.cardea.public_ip
}

output "ssh" {
  value = "ssh -i ${var.private_key_path} ubuntu@${aws_eip.cardea.public_ip}"
}

# Hands the server to Ansible:  terraform output -raw ansible_inventory > ../ansible/inventory.ini
output "ansible_inventory" {
  value = <<-EOT
    [cardea]
    ${aws_eip.cardea.public_ip} ansible_user=ubuntu ansible_ssh_private_key_file=${var.private_key_path}
  EOT
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
