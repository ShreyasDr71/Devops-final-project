// Every push to main goes through these stages (Jenkins polls GitHub every 2 minutes).
pipeline {
  agent any

  environment {
    // Local image registry (started by Ansible) that k3s pulls from
    REGISTRY = '127.0.0.1:5050'
    TAG = "${env.BUILD_NUMBER}"
    // kubectl runs as a container on the host network, using the k3s kubeconfig Ansible gave Jenkins
    KUBECTL = 'docker run --rm -i --network host --user 1000 -v /var/jenkins_home/.kube/config:/kubeconfig:ro -e KUBECONFIG=/kubeconfig rancher/kubectl:v1.36.2'
  }

  stages {
    stage('Secret scan (Gitleaks)') {
      steps {
        // Fails the build if a password / API key was committed
        sh 'docker run --rm -v "$WORKSPACE:/repo" zricethezav/gitleaks:v8.21.2 detect --no-git --source /repo --redact -v'
      }
    }

    stage('Unit tests') {
      steps {
        sh '''
          docker run --rm -v "$WORKSPACE:/usr/src" -w /usr/src python:3.12-slim sh -c "
            pip install -q -r hub/requirements.txt -r agent/requirements.txt pytest pytest-cov &&
            pytest -p no:cacheprovider hub agent --cov=hub --cov=agent --cov-report=xml --junitxml=test-results.xml"
        '''
      }
      post {
        always { junit 'test-results.xml' }
      }
    }

    stage('Code quality (SonarQube)') {
      steps {
        withCredentials([string(credentialsId: 'sonar-token', variable: 'SONAR_TOKEN')]) {
          // qualitygate.wait=true makes the build fail if the Quality Gate fails
          sh '''
            docker run --rm --network devops -v "$WORKSPACE:/usr/src" \
              -e SONAR_HOST_URL=http://sonarqube:9000 -e SONAR_TOKEN \
              sonarsource/sonar-scanner-cli:11.1 -Dsonar.qualitygate.wait=true
          '''
        }
      }
    }

    stage('Build images') {
      steps {
        sh '''
          docker build -t $REGISTRY/cardea-hub:$TAG hub
          docker build -t $REGISTRY/cardea-agent:$TAG agent
        '''
      }
    }

    stage('Security scan (Trivy)') {
      steps {
        sh '''
          TRIVY="docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v trivy-cache:/root/.cache aquasec/trivy:0.56.2"

          for image in $REGISTRY/cardea-hub:$TAG $REGISTRY/cardea-agent:$TAG; do
            # Show HIGH + CRITICAL issues, but only fail on fixable CRITICAL ones
            $TRIVY image --severity HIGH,CRITICAL --ignore-unfixed "$image"
            $TRIVY image --severity CRITICAL --ignore-unfixed --exit-code 1 --quiet "$image"
          done
        '''
        // Report misconfigurations in Dockerfiles, Kubernetes manifests and Terraform (does not fail the build)
        sh 'docker run --rm -v "$WORKSPACE:/src" aquasec/trivy:0.56.2 config --severity HIGH,CRITICAL /src'
      }
    }

    stage('Push images') {
      steps {
        sh '''
          docker push $REGISTRY/cardea-hub:$TAG
          docker push $REGISTRY/cardea-agent:$TAG
        '''
      }
    }

    // ---------- Blue-green deployment (k8s/blue-green.sh) ----------
    // The new build goes to the idle colour while users stay on the live one.

    stage('Deploy to idle colour') {
      steps {
        sh 'sh k8s/blue-green.sh deploy'
      }
    }

    stage('Test idle colour') {
      steps {
        // Health check through the <app>-preview Services, before any user traffic
        sh 'sh k8s/blue-green.sh test'
      }
    }

    stage('Switch traffic') {
      steps {
        sh 'sh k8s/blue-green.sh switch'
      }
    }

    stage('Smoke test') {
      steps {
        // Through the same ports devices use, now served by the new colour
        sh '''
          for port in 5000 5001 5002; do
            docker run --rm --network host curlimages/curl:8.10.1 -fsS --retry 10 --retry-delay 3 --retry-all-errors http://127.0.0.1:$port/health
          done
        '''
      }
      post {
        // The old colour is still running at this point, so rolling back is instant
        failure { sh 'sh k8s/blue-green.sh rollback' }
      }
    }

    stage('Retire old colour') {
      steps {
        // Scaled to 0, not deleted: 'sh k8s/blue-green.sh rollback' can bring it back
        sh 'sh k8s/blue-green.sh retire'
      }
    }
  }

  post {
    success {
      sh 'sh k8s/blue-green.sh status'
      echo "Cardea running on Kubernetes: hub http://${env.SERVER_IP}:5000, devices :5001 and :5002"
    }
    failure { echo 'Build failed - check the stage that is red.' }
  }
}
