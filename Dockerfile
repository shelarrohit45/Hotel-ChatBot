FROM python:3.12-slim

WORKDIR /app

COPY HOTEL-CHATBOT-MCP/requirements.txt /tmp/mcp-requirements.txt
COPY HOTEL-CHATBOT-CLIENT/requirements.txt /tmp/client-requirements.txt
RUN pip install --no-cache-dir -r /tmp/client-requirements.txt -r /tmp/mcp-requirements.txt

COPY HOTEL-CHATBOT-MCP /app/HOTEL-CHATBOT-MCP
COPY HOTEL-CHATBOT-CLIENT /app/HOTEL-CHATBOT-CLIENT

WORKDIR /app/HOTEL-CHATBOT-CLIENT

ENV PYTHONUNBUFFERED=1
ENV MCP_SERVER_PYTHON=python
ENV MCP_SERVER_SCRIPT=/app/HOTEL-CHATBOT-MCP/server.py
ENV MCP_SERVER_CWD=/app/HOTEL-CHATBOT-MCP
ENV PORT=8000

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
