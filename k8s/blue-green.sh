#!/bin/sh
# Blue-green deployment of the Cardea apps (hub, agent-a, agent-b) on k3s.
#
# Every app has two Deployments, <app>-blue and <app>-green. The public Service <app> sends all
# traffic to one colour (the live one). A new build goes to the other colour (the idle one), is
# tested through <app>-preview, and only then does the public Service switch over. The old colour
# is scaled to 0 but kept, so rolling back is just switching the Service back.
#
# Usage (from the Jenkinsfile, run in the workspace):
#   sh k8s/blue-green.sh deploy|test|switch|rollback|retire|status
# Needs KUBECTL, TAG and SERVER_IP in the environment.
set -eu

APPS="hub agent-a agent-b"
STATE=.bluegreen          # remembers LIVE and IDLE between pipeline stages
CURL="docker run --rm --network host curlimages/curl:8.10.1 -fsS --retry 10 --retry-delay 3 --retry-all-errors"

k() { $KUBECTL -n cardea "$@"; }

live_colour() { k get svc hub -o jsonpath='{.spec.selector.color}' 2>/dev/null || true; }

set_colour() {  # set_colour <service> <app> <colour>
  k patch svc "$1" -p "{\"spec\":{\"selector\":{\"app\":\"$2\",\"color\":\"$3\"}}}"
}

case "${1:-}" in
  deploy)
    LIVE=$(live_colour)
    if [ -z "$LIVE" ]; then
      # First blue-green build: the old single Deployments are still serving. Label their pods
      # "green" so the Services keep sending traffic to them, and deploy the new build as blue.
      LIVE=green
      for app in $APPS; do k label pods -l "app=$app" color=green --overwrite || true; done
    fi
    if [ "$LIVE" = blue ]; then IDLE=green; else IDLE=blue; fi
    printf 'LIVE=%s\nIDLE=%s\n' "$LIVE" "$IDLE" > "$STATE"
    echo "Live colour: $LIVE - deploying build $TAG to the idle colour: $IDLE"

    cat k8s/namespace.yaml k8s/hub.yaml k8s/agents.yaml k8s/logging.yaml \
      | sed -e "s/__IMAGE_TAG__/$TAG/g" -e "s/__SERVER_IP__/$SERVER_IP/g" \
            -e "s/__COLOR__/$IDLE/g" -e "s/__LIVE_COLOR__/$LIVE/g" \
      | $KUBECTL apply -f -
    for app in $APPS; do k rollout status "deployment/$app-$IDLE" --timeout=180s; done
    ;;

  test)
    . "./$STATE"
    echo "Testing the idle colour ($IDLE) before it gets any real traffic"
    for app in $APPS; do
      ip=$(k get svc "$app-preview" -o jsonpath='{.spec.clusterIP}')
      port=$(k get svc "$app-preview" -o jsonpath='{.spec.ports[0].port}')
      echo "$app-preview ($IDLE) -> http://$ip:$port/health"
      $CURL "http://$ip:$port/health"; echo
    done
    ;;

  switch)
    . "./$STATE"
    echo "Switching live traffic from $LIVE to $IDLE"
    for app in $APPS; do
      set_colour "$app" "$app" "$IDLE"
      set_colour "$app-preview" "$app" "$LIVE"
    done
    k get svc -o 'custom-columns=SERVICE:.metadata.name,TYPE:.spec.type,COLOUR:.spec.selector.color,PORT:.spec.ports[0].port'
    ;;

  rollback)
    . "./$STATE"
    echo "Rolling back: sending live traffic to $LIVE again"
    for app in $APPS; do
      # After 'retire' the old colour is at 0 replicas; bring it back first
      k scale "deployment/$app-$LIVE" --replicas=1 2>/dev/null && k rollout status "deployment/$app-$LIVE" --timeout=180s || true
      set_colour "$app" "$app" "$LIVE"
      set_colour "$app-preview" "$app" "$IDLE"
    done
    ;;

  retire)
    . "./$STATE"
    echo "New colour $IDLE is live. Scaling $LIVE to 0 (kept for rollback)"
    for app in $APPS; do
      k scale "deployment/$app-$LIVE" --replicas=0 2>/dev/null || true
      # The single Deployments from before blue-green are no longer needed
      k delete deployment "$app" --ignore-not-found
    done
    k get deploy,pods -o wide
    ;;

  status)
    k get deploy -o 'custom-columns=DEPLOYMENT:.metadata.name,COLOUR:.spec.selector.matchLabels.color,WANTED:.spec.replicas,READY:.status.readyReplicas,IMAGE:.spec.template.spec.containers[0].image'
    k get svc -o 'custom-columns=SERVICE:.metadata.name,TYPE:.spec.type,COLOUR:.spec.selector.color,PORT:.spec.ports[0].port'
    ;;

  *)
    echo "usage: $0 deploy|test|switch|rollback|retire|status" >&2
    exit 2
    ;;
esac
