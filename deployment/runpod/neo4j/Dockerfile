FROM neo4j:5-community

COPY neo4j.dump /opt/bootstrap/neo4j.dump
COPY restore-and-start.sh /opt/bootstrap/restore-and-start.sh

RUN chmod +x /opt/bootstrap/restore-and-start.sh

ENTRYPOINT ["tini", "-g", "--", "/opt/bootstrap/restore-and-start.sh"]
CMD ["neo4j"]
