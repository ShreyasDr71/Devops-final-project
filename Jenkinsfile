// Every push to main goes through these stages (Jenkins polls GitHub every 2 minutes).
pipeline {
  agent any

  environment {
    COMPOSE_PROJECT_NAME = 'cardea'
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
        sh 'docker compose build'
      }
    }

    stage('Security scan (Trivy)') {
      steps {
        sh '''
          TRIVY="docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v trivy-cache:/root/.cache aquasec/trivy:0.56.2"

          for image in cardea-hub:latest cardea-agent:latest; do
            # Show HIGH + CRITICAL issues, but only fail on fixable CRITICAL ones
            $TRIVY image --severity HIGH,CRITICAL --ignore-unfixed "$image"
            $TRIVY image --severity CRITICAL --ignore-unfixed --exit-code 1 --quiet "$image"
          done
        '''
        // Report misconfigurations in Dockerfiles and Terraform (does not fail the build)
        sh 'docker run --rm -v "$WORKSPACE:/src" aquasec/trivy:0.56.2 config --severity HIGH,CRITICAL /src'
      }
    }

    stage('Deploy') {
      steps {
        sh 'docker compose -f docker-compose.yml -f docker-compose.aws.yml up -d'
      }
    }

    stage('Smoke test') {
      steps {
        sh '''
          sleep 10
          docker run --rm --network devops curlimages/curl:8.10.1 -fsS http://hub:5000/health
          docker run --rm --network devops curlimages/curl:8.10.1 -fsS http://agent-a:5001/health
          docker run --rm --network devops curlimages/curl:8.10.1 -fsS http://agent-b:5002/health
        '''
      }
    }
  }

  post {
    success { echo "Cardea deployed: http://${env.SERVER_IP}:5001 and http://${env.SERVER_IP}:5002" }
    failure { echo 'Build failed - check the stage that is red.' }
  }
}
