# Step 1: Use an official Node.js runtime as a parent image
FROM node:latest

# Step 2: Set the working directory in the container
WORKDIR /usr/src/app

# Step 3: Copy the package.json files and install dependencies
COPY package*.json ./
RUN npm install

# Step 4: Bundle app source inside Docker image
COPY . .

# Step 5: Your app binds to port 3000, so you'll use the EXPOSE instruction to have it mapped by the docker daemon
EXPOSE 3000

# Step 6: Define the command to run your app
CMD ["npm", "start"]
