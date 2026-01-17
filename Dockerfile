FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

ENV PORT=3000
ENV MONGO_URI=mongodb://mongodb:27017

EXPOSE 3000

CMD ["python", "main.py"]