# Deploying the sync server (Oracle Always Free)

## 1. Accounts (human)

- Oracle Cloud: https://signup.cloud.oracle.com — free tier, card required
  for identity but not charged. Pick a home region close to you.
- DuckDNS: https://www.duckdns.org — sign in, create a subdomain
  (e.g. `taimoor-brain`), note the token.

## 2. Provision the VM (human, Oracle console)

- Compute → Instances → Create instance
- Image: Ubuntu 24.04. Shape: Ampere A1 Flex (Always Free): 2 OCPU, 12 GB.
- Add your SSH public key. Create.
- Networking: in the instance's subnet security list, add ingress rules for
  TCP 80 and 443 from 0.0.0.0/0. (22 is open by default.)
- Note the public IP. In DuckDNS, point your subdomain at it.

## 3. Install Docker (SSH into the VM)

    sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2
    sudo usermod -aG docker $USER && newgrp docker
    # Oracle Ubuntu images ship restrictive iptables; open the ports:
    sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT
    sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
    sudo apt-get install -y iptables-persistent && sudo netfilter-persistent save

## 4. Deploy the stack

    # copy the server/ directory to the VM (scp, or clone the repo), then:
    cd server
    cp .env.example .env && nano .env    # fill every value
    docker compose up -d

## 5. Verify

    docker compose ps                    # all three services Up
    source .env
    curl -u "$COUCHDB_USER:$COUCHDB_PASSWORD" https://$DOMAIN/_up
    # expect: {"status":"ok"}

Then create the LiveSync database:

    curl -u "$COUCHDB_USER:$COUCHDB_PASSWORD" -X PUT https://$DOMAIN/tesseract
    # expect: {"ok":true}

## Maintenance

- `docker compose pull && docker compose up -d` occasionally for updates.
- CouchDB data lives in the `couchdb-data` volume. For backups, snapshot the
  VM's boot volume in the Oracle console, or:

      docker run --rm -v server_couchdb-data:/d -v $PWD:/b alpine tar czf /b/couch-backup.tgz /d

- Remember: note contents are end-to-end encrypted by LiveSync; the server
  only ever sees ciphertext. The E2E passphrase lives in your password
  manager, not on this VM.
