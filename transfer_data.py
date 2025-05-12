import hashlib
import json
import logging
import pickle
import socket
import threading
import time
import tkinter as tk
import uuid
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("P2PFileTransfer")

# Constants
BUFFER_SIZE = 4096
DISCOVERY_PORT = 55555  # Port for user discovery
TRANSFER_PORT_RANGE = (55556, 55656)  # Port range for file transfer
MAX_RETRIES = 5
DEFAULT_DOWNLOAD_PATH = Path.home() / "Downloads" / "P2PReceivedFiles"


class User:
    """Represents a user"""

    def __init__(self, username, user_id, ip=None, port=None, is_online=False):
        self.username = username
        self.user_id = user_id
        self.ip = ip
        self.port = port
        self.is_online = is_online
        self.last_seen = time.time() if is_online else 0

    def __str__(self):
        status = "Online" if self.is_online else "Offline"
        return f"{self.username} ({status})"

    def to_dict(self):
        return {
            "username": self.username,
            "user_id": self.user_id,
            "ip": self.ip,
            "port": self.port,
            "is_online": self.is_online,
            "last_seen": self.last_seen
        }

    @classmethod
    def from_dict(cls, data):
        user = cls(data["username"], data["user_id"], data.get("ip"), data.get("port"), data.get("is_online", False))
        user.last_seen = data.get("last_seen", 0)
        return user


class FileTransferProtocol:
    """Implements the file transfer protocol"""

    # Message types
    DISCOVERY = 1
    DISCOVERY_RESPONSE = 2
    FRIEND_REQUEST = 3
    FRIEND_RESPONSE = 4
    FILE_REQUEST = 5
    FILE_RESPONSE = 6
    FILE_DATA = 7
    TRANSFER_COMPLETE = 8
    HEARTBEAT = 9
    ERROR = 10

    @staticmethod
    def create_message(msg_type, data):
        """Creates a message"""
        message = {
            "type": msg_type,
            "data": data,
            "timestamp": time.time()
        }
        return pickle.dumps(message)

    @staticmethod
    def parse_message(message_bytes):
        """Parses a message"""
        try:
            message = pickle.loads(message_bytes)
            return message["type"], message["data"], message.get("timestamp")
        except Exception as e:
            logger.error(f"Message parsing error: {e}")
            return FileTransferProtocol.ERROR, {"error": "Invalid message format"}, time.time()


class P2PFileTransfer:
    """Main class for P2P file transfer"""

    def __init__(self):
        # User configuration
        self.config_path = Path.home() / ".p2p_file_transfer"
        self.config_file = self.config_path / "config.json"
        self.friends_file = self.config_path / "friends.json"
        self.download_path = DEFAULT_DOWNLOAD_PATH

        # Ensure directories exist
        self.config_path.mkdir(exist_ok=True)
        self.download_path.mkdir(exist_ok=True, parents=True)

        # User information
        self.username = None
        self.user_id = None
        self.discovery_socket = None
        self.transfer_sockets = {}  # Transfer sockets dictionary {user_id: socket}
        self.friends = {}  # Friends dictionary {user_id: User}
        self.pending_friends = {}  # Pending friend requests {user_id: User}
        self.active_transfers = {}  # Active transfers {transfer_id: transfer_info}

        # Thread control
        self.running = False
        self.threads = []

        # Load configuration
        self.load_config()
        self.load_friends()

        # Initialize UI
        self.init_ui()

    def load_config(self):
        """Loads user configuration"""
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                self.username = config.get("username")
                self.user_id = config.get("user_id")
                download_path = config.get("download_path")
                if download_path:
                    self.download_path = Path(download_path)
                    self.download_path.mkdir(exist_ok=True, parents=True)
            except Exception as e:
                logger.error(f"Config loading error: {e}")
                self.username = None
                self.user_id = None

        # Create new user if no user ID or username
        if not self.username or not self.user_id:
            self.setup_new_user()

    def setup_new_user(self):
        """Sets up a new user"""
        # Create in command line; GUI version handles in UI initialization
        if not hasattr(self, 'root'):
            self.username = input("Please enter your username: ")
            # Generate an 8-digit unique UUID
            base_str = str(uuid.uuid4()) + str(int(time.time() * 1000))
            hash_obj = hashlib.sha256(base_str.encode())
            hash_hex = hash_obj.hexdigest()
            hash_int = int(hash_hex[:16], 16)
            self.user_id = str(hash_int % 10 ** 8).zfill(8)
            self.save_config()

    def save_config(self):
        """Saves user configuration"""
        config = {
            "username": self.username,
            "user_id": self.user_id,
            "download_path": str(self.download_path)
        }
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Config saving error: {e}")

    def load_friends(self):
        """Loads friends list"""
        if self.friends_file.exists():
            try:
                with open(self.friends_file, 'r', encoding='utf-8') as f:
                    friends_data = json.load(f)
                for friend_dict in friends_data:
                    user = User.from_dict(friend_dict)
                    self.friends[user.user_id] = user
            except Exception as e:
                logger.error(f"Friends list loading error: {e}")

    def save_friends(self):
        """Saves friends list"""
        friends_data = [friend.to_dict() for friend in self.friends.values()]
        try:
            with open(self.friends_file, 'w', encoding='utf-8') as f:
                json.dump(friends_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Friends list saving error: {e}")

    def init_discovery_socket(self):
        """Initializes discovery socket"""
        try:
            self.discovery_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.discovery_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.discovery_socket.bind(('0.0.0.0', DISCOVERY_PORT))
            logger.info(f"Discovery service listening on port {DISCOVERY_PORT}")
            return True
        except Exception as e:
            logger.error(f"Discovery socket initialization error: {e}")
            messagebox.showerror("Error", f"Unable to start discovery service: {e}")
            return False

    def start_services(self):
        """Starts all services"""
        if self.running:
            return

        # Initialize discovery socket
        if not self.init_discovery_socket():
            return

        self.running = True

        # Start discovery service thread
        discovery_thread = threading.Thread(target=self.discovery_service)
        discovery_thread.daemon = True
        discovery_thread.start()
        self.threads.append(discovery_thread)

        # Start heartbeat thread
        heartbeat_thread = threading.Thread(target=self.heartbeat_service)
        heartbeat_thread.daemon = True
        heartbeat_thread.start()
        self.threads.append(heartbeat_thread)

        # Update status
        self.status_var.set("Online")
        self.status_label.config(foreground="green")

        logger.info("All services started")

    def stop_services(self):
        """Stops all services"""
        self.running = False

        # Close all sockets
        if self.discovery_socket:
            self.discovery_socket.close()

        for socket_obj in self.transfer_sockets.values():
            socket_obj.close()

        self.transfer_sockets.clear()
        self.threads.clear()

        # Update status
        self.status_var.set("Offline")
        self.status_label.config(foreground="red")

        logger.info("All services stopped")

    def discovery_service(self):
        """Discovery service loop"""
        while self.running:
            try:
                data, addr = self.discovery_socket.recvfrom(BUFFER_SIZE)
                threading.Thread(target=self.handle_discovery_message, args=(data, addr)).start()
            except OSError:
                if self.running:  # Log only if not intentionally closed
                    logger.error("Discovery service socket error")
                break
            except Exception as e:
                logger.error(f"Discovery service error: {e}")

        logger.info("Discovery service stopped")

    def handle_discovery_message(self, data, addr):
        """Handles discovery messages"""
        try:
            msg_type, msg_data, _ = FileTransferProtocol.parse_message(data)

            if msg_type == FileTransferProtocol.DISCOVERY:
                # Another user is searching
                query_id = msg_data.get("query_id")
                if query_id == self.user_id:
                    # Someone is looking for me, send response
                    response_data = {
                        "user_id": self.user_id,
                        "username": self.username,
                        "query_id": query_id
                    }
                    response = FileTransferProtocol.create_message(
                        FileTransferProtocol.DISCOVERY_RESPONSE, response_data
                    )
                    self.discovery_socket.sendto(response, addr)
                    logger.info(f"Responded to discovery request from {addr}")

            elif msg_type == FileTransferProtocol.DISCOVERY_RESPONSE:
                # Received search response
                response_id = msg_data.get("user_id")
                response_name = msg_data.get("username")
                query_id = msg_data.get("query_id")

                if query_id == "broadcast" or query_id == response_id:
                    # Update friend status or add to search results
                    if response_id in self.friends:
                        self.friends[response_id].ip = addr[0]
                        self.friends[response_id].port = addr[1]
                        self.friends[response_id].is_online = True
                        self.friends[response_id].last_seen = time.time()
                        self.update_friends_list()
                        logger.info(f"Friend {response_name} is now online")
                    else:
                        # Add to search results
                        user = User(response_name, response_id, addr[0], addr[1], True)
                        self.search_results[response_id] = user
                        self.update_search_results()
                        logger.info(f"Discovered new user: {response_name}")

            elif msg_type == FileTransferProtocol.FRIEND_REQUEST:
                # Received friend request
                requester_id = msg_data.get("user_id")
                requester_name = msg_data.get("username")

                if requester_id not in self.friends and requester_id != self.user_id:
                    user = User(requester_name, requester_id, addr[0], addr[1], True)
                    self.pending_friends[requester_id] = user
                    self.update_pending_list()
                    logger.info(f"Received friend request from {requester_name}")

                    # Show notification
                    self.root.after(0, lambda: messagebox.showinfo(
                        "New Friend Request",
                        f"Received friend request from {requester_name}"))

            elif msg_type == FileTransferProtocol.FRIEND_RESPONSE:
                # Received friend response
                responder_id = msg_data.get("user_id")
                responder_name = msg_data.get("username")
                accepted = msg_data.get("accepted", False)

                if accepted:
                    # Friend request accepted
                    if responder_id not in self.friends:
                        user = User(responder_name, responder_id, addr[0], addr[1], True)
                        self.friends[responder_id] = user
                        self.save_friends()
                        self.update_friends_list()
                        logger.info(f"{responder_name} accepted your friend request")

                        # Show notification
                        self.root.after(0, lambda: messagebox.showinfo(
                            "Friend Request Accepted",
                            f"{responder_name} has accepted your friend request"))
                else:
                    # Friend request rejected
                    logger.info(f"{responder_name} rejected your friend request")

                    # Show notification
                    self.root.after(0, lambda: messagebox.showinfo(
                        "Friend Request Rejected",
                        f"{responder_name} has rejected your friend request"))

            elif msg_type == FileTransferProtocol.FILE_REQUEST:
                # Received file transfer request
                sender_id = msg_data.get("user_id")
                sender_name = msg_data.get("username")
                filename = msg_data.get("filename")
                filesize = msg_data.get("filesize")
                transfer_id = msg_data.get("transfer_id")

                if sender_id in self.friends:
                    # Update friend status
                    self.friends[sender_id].ip = addr[0]
                    self.friends[sender_id].port = addr[1]
                    self.friends[sender_id].is_online = True
                    self.friends[sender_id].last_seen = time.time()

                    # Show file transfer request
                    self.root.after(0, lambda: self.show_file_request(
                        sender_id, sender_name, filename, filesize, transfer_id))

            elif msg_type == FileTransferProtocol.FILE_RESPONSE:
                # Received file transfer response
                transfer_id = msg_data.get("transfer_id")
                accepted = msg_data.get("accepted", False)
                port = msg_data.get("port")

                if transfer_id in self.active_transfers:
                    transfer_info = self.active_transfers[transfer_id]
                    transfer_info["accepted"] = accepted

                    if accepted and port:
                        # Start sending file
                        transfer_info["remote_port"] = port
                        threading.Thread(
                            target=self.send_file,
                            args=(transfer_id,)
                        ).start()
                    else:
                        # File transfer rejected
                        self.root.after(0, lambda: messagebox.showinfo(
                            "File Transfer Canceled",
                            f"Your file transfer request was rejected"))
                        del self.active_transfers[transfer_id]

            elif msg_type == FileTransferProtocol.HEARTBEAT:
                # Received heartbeat message
                sender_id = msg_data.get("user_id")
                sender_name = msg_data.get("username")

                if sender_id in self.friends:
                    # Update friend status
                    self.friends[sender_id].ip = addr[0]
                    self.friends[sender_id].port = addr[1]
                    self.friends[sender_id].is_online = True
                    self.friends[sender_id].last_seen = time.time()
                    self.update_friends_list()

        except Exception as e:
            logger.error(f"Discovery message handling error: {e}")

    def heartbeat_service(self):
        """Heartbeat service: periodically sends heartbeat messages to all friends"""
        while self.running:
            try:
                # Send heartbeat message
                heartbeat_data = {
                    "user_id": self.user_id,
                    "username": self.username
                }
                heartbeat_msg = FileTransferProtocol.create_message(
                    FileTransferProtocol.HEARTBEAT, heartbeat_data
                )

                # Send to all known friends
                for friend_id, friend in list(self.friends.items()):
                    if friend.ip and friend.is_online:
                        try:
                            self.discovery_socket.sendto(
                                heartbeat_msg,
                                (friend.ip, DISCOVERY_PORT)
                            )
                        except Exception as e:
                            logger.error(f"Heartbeat send to {friend.username} error: {e}")

                # Check if friends are online
                current_time = time.time()
                for friend_id, friend in list(self.friends.items()):
                    if friend.is_online and (current_time - friend.last_seen) > 60:  # 60s no response = offline
                        friend.is_online = False
                        self.update_friends_list()

                # Send heartbeat every 10 seconds
                time.sleep(10)

            except Exception as e:
                logger.error(f"Heartbeat service error: {e}")
                time.sleep(10)  # Wait before retrying on error

        logger.info("Heartbeat service stopped")

    def search_user(self, user_id=None):
        """Searches for users"""
        if not self.running:
            messagebox.showwarning("Warning", "Please start the service first")
            return

        # Clear old search results
        self.search_results.clear()
        self.update_search_results()

        # Prepare search data
        if not user_id:
            user_id = self.search_entry.get().strip()

        # Broadcast search if empty
        if not user_id:
            user_id = "broadcast"

        search_data = {
            "user_id": self.user_id,
            "username": self.username,
            "query_id": user_id
        }

        search_msg = FileTransferProtocol.create_message(
            FileTransferProtocol.DISCOVERY, search_data
        )

        # Broadcast search request
        try:
            # Broadcast on LAN
            self.discovery_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self.discovery_socket.sendto(search_msg, ('<broadcast>', DISCOVERY_PORT))

            # Send to all known friend IPs
            for friend in self.friends.values():
                if friend.ip:
                    try:
                        self.discovery_socket.sendto(search_msg, (friend.ip, DISCOVERY_PORT))
                    except:
                        pass

            logger.info(f"Sent search request for ID: {user_id}")

            # Update status
            self.status_var.set(f"Searching for user: {user_id}")
            self.root.after(5000, lambda: self.status_var.set("Online" if self.running else "Offline"))

        except Exception as e:
            logger.error(f"User search error: {e}")
            messagebox.showerror("Error", f"Failed to search user: {e}")

    def send_friend_request(self, user_id):
        """Sends a friend request"""
        if not self.running:
            messagebox.showwarning("Warning", "Please start the service first")
            return

        if user_id in self.friends:
            messagebox.showinfo("Info", "This user is already your friend")
            return

        if user_id not in self.search_results:
            messagebox.showwarning("Warning", "User not found")
            return

        user = self.search_results[user_id]

        # Prepare friend request data
        request_data = {
            "user_id": self.user_id,
            "username": self.username
        }

        request_msg = FileTransferProtocol.create_message(
            FileTransferProtocol.FRIEND_REQUEST, request_data
        )

        # Send friend request
        try:
            self.discovery_socket.sendto(request_msg, (user.ip, DISCOVERY_PORT))
            logger.info(f"Sent friend request to {user.username}")
            messagebox.showinfo("Info", f"Friend request sent to {user.username}")
        except Exception as e:
            logger.error(f"Friend request send error: {e}")
            messagebox.showerror("Error", f"Failed to send friend request: {e}")

    def respond_friend_request(self, user_id, accept=True):
        """Responds to a friend request"""
        if not self.running:
            messagebox.showwarning("Warning", "Please start the service first")
            return

        if user_id not in self.pending_friends:
            messagebox.showwarning("Warning", "Friend request not found")
            return

        user = self.pending_friends[user_id]

        # Prepare friend response data
        response_data = {
            "user_id": self.user_id,
            "username": self.username,
            "accepted": accept
        }

        response_msg = FileTransferProtocol.create_message(
            FileTransferProtocol.FRIEND_RESPONSE, response_data
        )

        # Send friend response
        try:
            self.discovery_socket.sendto(response_msg, (user.ip, DISCOVERY_PORT))

            if accept:
                # Add as friend
                self.friends[user_id] = user
                self.save_friends()
                self.update_friends_list()
                logger.info(f"Accepted friend request from {user.username}")
            else:
                logger.info(f"Rejected friend request from {user.username}")

            # Remove from pending list
            del self.pending_friends[user_id]
            self.update_pending_list()

        except Exception as e:
            logger.error(f"Friend request response error: {e}")
            messagebox.showerror("Error", f"Failed to respond to friend request: {e}")

    def send_file_request(self, user_id):
        """Sends a file transfer request"""
        if not self.running:
            messagebox.showwarning("Warning", "Please start the service first")
            return

        if user_id not in self.friends:
            messagebox.showwarning("Warning", "This user is not your friend")
            return

        friend = self.friends[user_id]

        if not friend.is_online:
            messagebox.showwarning("Warning", "This friend is currently offline")
            return

        # Select file to send
        filepath = filedialog.askopenfilename(title="Select file to send")
        if not filepath:
            return

        file_info = Path(filepath)
        filename = file_info.name
        filesize = file_info.stat().st_size

        # Generate transfer ID
        transfer_id = str(uuid.uuid4())

        # Prepare file request data
        request_data = {
            "user_id": self.user_id,
            "username": self.username,
            "filename": filename,
            "filesize": filesize,
            "transfer_id": transfer_id
        }

        request_msg = FileTransferProtocol.create_message(
            FileTransferProtocol.FILE_REQUEST, request_data
        )

        # Add to active transfers
        self.active_transfers[transfer_id] = {
            "filepath": filepath,
            "filename": filename,
            "filesize": filesize,
            "receiver_id": user_id,
            "receiver_name": friend.username,
            "receiver_ip": friend.ip,
            "status": "pending",
            "progress": 0
        }

        # Send file request
        try:
            self.discovery_socket.sendto(request_msg, (friend.ip, DISCOVERY_PORT))
            logger.info(f"Sent file transfer request to {friend.username}: {filename}")
            messagebox.showinfo("Info", f"File transfer request sent to {friend.username}")
        except Exception as e:
            logger.error(f"File request send error: {e}")
            messagebox.showerror("Error", f"Failed to send file request: {e}")
            del self.active_transfers[transfer_id]

    def show_file_request(self, sender_id, sender_name, filename, filesize, transfer_id):
        """Shows file transfer request dialog"""
        # Format file size
        size_str = self.format_size(filesize)

        # Create dialog
        dialog = tk.Toplevel(self.root)
        dialog.title("File Transfer Request")
        dialog.geometry("400x250")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        # Add content
        tk.Label(
            dialog,
            text=f"{sender_name} wants to send you a file",
            font=("Helvetica", 12, "bold")
        ).pack(pady=10)

        tk.Label(
            dialog,
            text=f"Filename: {filename}"
        ).pack(pady=5)

        tk.Label(
            dialog,
            text=f"Size: {size_str}"
        ).pack(pady=5)

        # Save path selection
        tk.Label(
            dialog,
            text="Save to:"
        ).pack(pady=5)

        save_path_var = tk.StringVar(value=str(self.download_path / filename))
        save_path_entry = tk.Entry(dialog, textvariable=save_path_var, width=40)
        save_path_entry.pack(pady=5)

        def browse_save_path():
            """Opens a dialog for the user to select a save location for the file"""
            save_path = filedialog.asksaveasfilename(
                initialfile=filename,
                initialdir=str(self.download_path),
                title="Select Save Location",
                defaultextension=Path(filename).suffix
            )
            if save_path:
                save_path_var.set(save_path)

        tk.Button(
            dialog,
            text="Browse",
            command=browse_save_path
        ).pack(pady=5)

        # Button frame
        btn_frame = tk.Frame(dialog)
        btn_frame.pack(pady=20)

        # Accept button
        tk.Button(
            btn_frame,
            text="Accept",
            width=10,
            command=lambda: self.respond_file_request(
                dialog, sender_id, sender_name, filename, filesize, transfer_id, True, save_path_var.get()
            )
        ).pack(side=tk.LEFT, padx=10)

        # Reject button
        tk.Button(
            btn_frame,
            text="Reject",
            width=10,
            command=lambda: self.respond_file_request(
                dialog, sender_id, sender_name, filename, filesize, transfer_id, False, None
            )
        ).pack(side=tk.LEFT, padx=10)

    def respond_file_request(self, dialog, sender_id, sender_name, filename, filesize, transfer_id, accept, save_path):
        """Responds to file transfer request"""
        dialog.destroy()

        if not accept:
            # Send reject response
            response_data = {
                "transfer_id": transfer_id,
                "accepted": False
            }
            response_msg = FileTransferProtocol.create_message(
                FileTransferProtocol.FILE_RESPONSE, response_data
            )
            try:
                self.discovery_socket.sendto(
                    response_msg,
                    (self.friends[sender_id].ip, DISCOVERY_PORT)
                )
                logger.info(f"Rejected file transfer request from {sender_name}")
                return
            except Exception as e:
                logger.error(f"File transfer reject response send error: {e}")
                return

        # Validate save path
        if not save_path:
            messagebox.showerror("Error", "Please select a valid save location")
            return

        try:
            save_path = Path(save_path)
            # Ensure save directory exists
            save_path.parent.mkdir(parents=True, exist_ok=True)

            # Find available port for file transfer
            transfer_port = self.find_available_port()
            if not transfer_port:
                messagebox.showerror("Error", "No available transfer port found")
                return

            # Create transfer socket
            transfer_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            transfer_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            transfer_socket.bind(('0.0.0.0', transfer_port))
            transfer_socket.listen(1)

            # Save active transfer information
            self.active_transfers[transfer_id] = {
                "filepath": str(save_path),
                "filename": filename,
                "filesize": filesize,
                "sender_id": sender_id,
                "sender_name": sender_name,
                "sender_ip": self.friends[sender_id].ip,
                "status": "waiting",
                "progress": 0,
                "socket": transfer_socket
            }

            # Send accept response
            response_data = {
                "transfer_id": transfer_id,
                "accepted": True,
                "port": transfer_port
            }
            response_msg = FileTransferProtocol.create_message(
                FileTransferProtocol.FILE_RESPONSE, response_data
            )
            self.discovery_socket.sendto(
                response_msg,
                (self.friends[sender_id].ip, DISCOVERY_PORT)
            )

            # Start receiving thread
            threading.Thread(
                target=self.receive_file,
                args=(transfer_id,)
            ).start()

            logger.info(f"Accepted file transfer request from {sender_name} on port {transfer_port}")

        except Exception as e:
            logger.error(f"File transfer request response error: {e}")
            messagebox.showerror("Error", f"Failed to respond to file transfer request: {e}")

    def find_available_port(self):
        """Finds an available transfer port"""
        for port in range(TRANSFER_PORT_RANGE[0], TRANSFER_PORT_RANGE[1]):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.bind(('0.0.0.0', port))
                sock.close()
                return port
            except:
                continue
        return None

    def send_file(self, transfer_id):
        """Sends a file"""
        if transfer_id not in self.active_transfers:
            return

        transfer_info = self.active_transfers[transfer_id]
        filepath = transfer_info["filepath"]
        filesize = transfer_info["filesize"]
        receiver_ip = transfer_info["receiver_ip"]
        remote_port = transfer_info["remote_port"]

        # Update transfer status
        transfer_info["status"] = "connecting"
        self.update_transfers_list()

        # Try connecting to receiver
        sock = None
        for _ in range(MAX_RETRIES):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(10)
                sock.connect((receiver_ip, remote_port))
                break
            except Exception as e:
                logger.error(f"Connection to receiver failed, retrying: {e}")
                time.sleep(1)

        if not sock:
            # Connection failed
            transfer_info["status"] = "failed"
            self.update_transfers_list()
            self.root.after(0, lambda: messagebox.showerror(
                "Transfer Failed",
                f"Unable to connect to receiver"))
            return

        try:
            # Update transfer status
            transfer_info["status"] = "transferring"
            self.update_transfers_list()

            # Send file data
            with open(filepath, 'rb') as f:
                sent_bytes = 0
                while sent_bytes < filesize:
                    # Read data chunk
                    chunk = f.read(BUFFER_SIZE)
                    if not chunk:
                        break

                    # Send data chunk
                    sock.sendall(chunk)

                    # Update progress
                    sent_bytes += len(chunk)
                    progress = int(sent_bytes * 100 / filesize)
                    transfer_info["progress"] = progress
                    self.update_transfers_list()

            # Transfer completed
            transfer_info["status"] = "completed"
            transfer_info["progress"] = 100
            self.update_transfers_list()

            # Notify receiver of completion
            complete_data = {
                "transfer_id": transfer_id,
                "status": "completed"
            }
            complete_msg = FileTransferProtocol.create_message(
                FileTransferProtocol.TRANSFER_COMPLETE, complete_data
            )
            sock.sendall(complete_msg)

            logger.info(f"File {Path(filepath).name} sent successfully")

            # Show notification
            self.root.after(0, lambda: messagebox.showinfo(
                "Transfer Completed",
                f"File {Path(filepath).name} sent successfully"))

        except Exception as e:
            # Transfer failed
            transfer_info["status"] = "failed"
            self.update_transfers_list()
            logger.error(f"File send error: {e}")

            # Show notification
            self.root.after(0, lambda: messagebox.showerror(
                "Transfer Failed",
                f"Failed to send file: {e}"))

        finally:
            # Close socket
            sock.close()

    def receive_file(self, transfer_id):
        """Receives a file"""
        if transfer_id not in self.active_transfers:
            return

        transfer_info = self.active_transfers[transfer_id]
        filepath = transfer_info["filepath"]
        filesize = transfer_info["filesize"]
        transfer_socket = transfer_info["socket"]

        # Update transfer status
        transfer_info["status"] = "waiting"
        self.update_transfers_list()

        try:
            # Wait for sender connection
            transfer_socket.settimeout(30)  # 30s timeout
            client_socket, client_address = transfer_socket.accept()

            # Update transfer status
            transfer_info["status"] = "transferring"
            self.update_transfers_list()

            # Receive file data
            with open(filepath, 'wb') as f:
                received_bytes = 0
                while received_bytes < filesize:
                    # Receive data chunk
                    chunk = client_socket.recv(BUFFER_SIZE)
                    if not chunk:
                        break

                    # Write to file
                    f.write(chunk)

                    # Update progress
                    received_bytes += len(chunk)
                    progress = int(received_bytes * 100 / filesize)
                    transfer_info["progress"] = progress
                    self.update_transfers_list()

            # Check file integrity
            if received_bytes >= filesize:
                # Transfer completed
                transfer_info["status"] = "completed"
                transfer_info["progress"] = 100
                self.update_transfers_list()

                logger.info(f"File {Path(filepath).name} received successfully")

                # Show notification
                self.root.after(0, lambda: messagebox.showinfo(
                    "Transfer Completed",
                    f"File {Path(filepath).name} received successfully\nSaved to: {filepath}"))
            else:
                # File incomplete
                transfer_info["status"] = "incomplete"
                self.update_transfers_list()

                logger.warning(f"File {Path(filepath).name} received incompletely")

                # Show notification
                self.root.after(0, lambda: messagebox.showwarning(
                    "Transfer Incomplete",
                    f"File {Path(filepath).name} received incompletely"))

        except socket.timeout:
            # Timeout
            transfer_info["status"] = "timeout"
            self.update_transfers_list()
            logger.error(f"File receive timeout")

            # Show notification
            self.root.after(0, lambda: messagebox.showerror(
                "Transfer Timeout",
                f"File receive timed out"))

        except Exception as e:
            # Receive failed
            transfer_info["status"] = "failed"
            self.update_transfers_list()
            logger.error(f"File receive error: {e}")

            # Show notification
            self.root.after(0, lambda: messagebox.showerror(
                "Transfer Failed",
                f"Failed to receive file: {e}"))

        finally:
            # Close sockets
            try:
                client_socket.close()
            except:
                pass
            transfer_socket.close()

    def format_size(self, size):
        """Formats file size"""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024:
                return f"{size:.2f} {unit}"
            size /= 1024
        return f"{size:.2f} PB"

    def init_ui(self):
        """Initializes user interface"""
        self.root = tk.Tk()
        self.root.title("P2P File Transfer")
        self.root.geometry("800x600")
        self.root.resizable(True, True)

        # Prompt for username and ID if not set
        if not self.username or not self.user_id:
            self.show_setup_dialog()

        # Main frame
        main_frame = tk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Left frame - Friends list and pending requests
        left_frame = tk.Frame(main_frame, width=250)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, padx=5, pady=5)

        # Status frame
        status_frame = tk.Frame(left_frame)
        status_frame.pack(fill=tk.X, pady=5)

        # User information
        tk.Label(status_frame, text=f"Username: {self.username}").pack(anchor=tk.W)
        tk.Label(status_frame, text=f"ID: {self.user_id}").pack(anchor=tk.W)

        # Status label
        status_label_frame = tk.Frame(status_frame)
        status_label_frame.pack(fill=tk.X, pady=5)

        tk.Label(status_label_frame, text="Status:").pack(side=tk.LEFT)
        self.status_var = tk.StringVar(value="Offline")
        self.status_label = tk.Label(status_label_frame, textvariable=self.status_var, foreground="red")
        self.status_label.pack(side=tk.LEFT, padx=5)

        # Start/Stop buttons
        button_frame = tk.Frame(left_frame)
        button_frame.pack(fill=tk.X, pady=5)

        self.start_button = tk.Button(button_frame, text="Start Service", command=self.start_services)
        self.start_button.pack(side=tk.LEFT, padx=5)

        self.stop_button = tk.Button(button_frame, text="Stop Service", command=self.stop_services)
        self.stop_button.pack(side=tk.LEFT, padx=5)

        # Friends notebook
        friends_notebook = ttk.Notebook(left_frame)
        friends_notebook.pack(fill=tk.BOTH, expand=True, pady=5)

        # Friends list page
        friends_frame = tk.Frame(friends_notebook)
        friends_notebook.add(friends_frame, text="Friends")

        # Friends list
        friends_list_frame = tk.Frame(friends_frame)
        friends_list_frame.pack(fill=tk.BOTH, expand=True)

        self.friends_listbox = tk.Listbox(friends_list_frame)
        self.friends_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        friends_scrollbar = tk.Scrollbar(friends_list_frame, orient=tk.VERTICAL, command=self.friends_listbox.yview)
        friends_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.friends_listbox.config(yscrollcommand=friends_scrollbar.set)

        # Friends operation buttons
        friends_button_frame = tk.Frame(friends_frame)
        friends_button_frame.pack(fill=tk.X, pady=5)

        self.send_file_button = tk.Button(
            friends_button_frame,
            text="Send File",
            command=lambda: self.send_file_request(self.get_selected_friend_id())
        )
        self.send_file_button.pack(side=tk.LEFT, padx=5)

        self.delete_friend_button = tk.Button(
            friends_button_frame,
            text="Delete Friend",
            command=self.delete_friend
        )
        self.delete_friend_button.pack(side=tk.LEFT, padx=5)

        # Pending requests page
        pending_frame = tk.Frame(friends_notebook)
        friends_notebook.add(pending_frame, text="Requests")

        # Pending requests list
        pending_list_frame = tk.Frame(pending_frame)
        pending_list_frame.pack(fill=tk.BOTH, expand=True)

        self.pending_listbox = tk.Listbox(pending_list_frame)
        self.pending_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        pending_scrollbar = tk.Scrollbar(pending_list_frame, orient=tk.VERTICAL, command=self.pending_listbox.yview)
        pending_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.pending_listbox.config(yscrollcommand=pending_scrollbar.set)

        # Pending requests operation buttons
        pending_button_frame = tk.Frame(pending_frame)
        pending_button_frame.pack(fill=tk.X, pady=5)

        self.accept_button = tk.Button(
            pending_button_frame,
            text="Accept",
            command=lambda: self.respond_friend_request(self.get_selected_pending_id(), True)
        )
        self.accept_button.pack(side=tk.LEFT, padx=5)

        self.reject_button = tk.Button(
            pending_button_frame,
            text="Reject",
            command=lambda: self.respond_friend_request(self.get_selected_pending_id(), False)
        )
        self.reject_button.pack(side=tk.LEFT, padx=5)

        # Right frame - Search and transfers
        right_frame = tk.Frame(main_frame)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Search frame
        search_frame = tk.LabelFrame(right_frame, text="Search User")
        search_frame.pack(fill=tk.X, pady=5)

        # Search input and button
        search_input_frame = tk.Frame(search_frame)
        search_input_frame.pack(fill=tk.X, padx=5, pady=5)

        tk.Label(search_input_frame, text="User ID:").pack(side=tk.LEFT)
        self.search_entry = tk.Entry(search_input_frame)
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        self.search_button = tk.Button(search_input_frame, text="Search", command=lambda: self.search_user())
        self.search_button.pack(side=tk.LEFT)

        # Search results list
        search_results_frame = tk.Frame(search_frame)
        search_results_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.search_listbox = tk.Listbox(search_results_frame)
        self.search_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        search_scrollbar = tk.Scrollbar(search_results_frame, orient=tk.VERTICAL, command=self.search_listbox.yview)
        search_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.search_listbox.config(yscrollcommand=search_scrollbar.set)

        # Add friend button
        self.add_friend_button = tk.Button(
            search_frame,
            text="Add as Friend",
            command=lambda: self.send_friend_request(self.get_selected_search_id())
        )
        self.add_friend_button.pack(pady=5)

        # Transfers frame
        transfers_frame = tk.LabelFrame(right_frame, text="Transfer Status")
        transfers_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        # Transfers list
        transfers_list_frame = tk.Frame(transfers_frame)
        transfers_list_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Use Treeview for more information
        self.transfers_tree = ttk.Treeview(
            transfers_list_frame,
            columns=("filename", "size", "peer", "status", "progress"),
            show="headings"
        )

        # Set columns
        self.transfers_tree.heading("filename", text="Filename")
        self.transfers_tree.heading("size", text="Size")
        self.transfers_tree.heading("peer", text="Peer")
        self.transfers_tree.heading("status", text="Status")
        self.transfers_tree.heading("progress", text="Progress")

        self.transfers_tree.column("filename", width=150)
        self.transfers_tree.column("size", width=80)
        self.transfers_tree.column("peer", width=100)
        self.transfers_tree.column("status", width=80)
        self.transfers_tree.column("progress", width=80)

        self.transfers_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        transfers_scrollbar = tk.Scrollbar(transfers_list_frame, orient=tk.VERTICAL, command=self.transfers_tree.yview)
        transfers_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.transfers_tree.config(yscrollcommand=transfers_scrollbar.set)

        # Clear completed button
        self.clear_button = tk.Button(
            transfers_frame,
            text="Clear Completed",
            command=self.clear_completed_transfers
        )
        self.clear_button.pack(pady=5)

        # Initialize internal state
        self.search_results = {}  # Search results {user_id: User}
        self.pending_friends = {}  # Pending friend requests {user_id: User}

        # Update lists
        self.update_friends_list()
        self.update_pending_list()
        self.update_search_results()

        # Protocol handlers binding
        self.protocol_handlers = {
            FileTransferProtocol.DISCOVERY: self.handle_discovery_message,
            FileTransferProtocol.DISCOVERY_RESPONSE: self.handle_discovery_response,
            FileTransferProtocol.FRIEND_REQUEST: self.handle_friend_request,
            FileTransferProtocol.FRIEND_RESPONSE: self.handle_friend_response,
            FileTransferProtocol.FILE_REQUEST: self.handle_file_request,
            FileTransferProtocol.FILE_RESPONSE: self.handle_file_response,
            FileTransferProtocol.TRANSFER_COMPLETE: self.handle_transfer_complete,
            FileTransferProtocol.HEARTBEAT: self.handle_heartbeat,
            FileTransferProtocol.ERROR: self.handle_error
        }

        # Window close event
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def show_setup_dialog(self):
        """Shows initial setup dialog"""
        dialog = tk.Toplevel()
        dialog.title("Initial Setup")
        dialog.geometry("300x150")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        tk.Label(dialog, text="Please set your username:").pack(pady=(20, 5))

        name_entry = tk.Entry(dialog, width=30)
        name_entry.pack(pady=5)
        name_entry.focus_set()

        # Confirm button
        def confirm():
            username = name_entry.get().strip()
            if username:
                self.username = username
                # Generate an 8-digit unique UUID
                base_str = str(uuid.uuid4()) + str(int(time.time() * 1000))
                hash_obj = hashlib.sha256(base_str.encode())
                hash_hex = hash_obj.hexdigest()
                hash_int = int(hash_hex[:16], 16)
                self.user_id = str(hash_int % 10 ** 8).zfill(8)
                self.save_config()
                dialog.destroy()
            else:
                messagebox.showwarning("Warning", "Username cannot be empty")

        tk.Button(dialog, text="Confirm", command=confirm).pack(pady=10)

        # Make dialog modal
        self.root.wait_window(dialog)

    def get_selected_friend_id(self):
        """Gets selected friend ID"""
        selection = self.friends_listbox.curselection()
        if not selection:
            messagebox.showinfo("Info", "Please select a friend first")
            return None

        # Get friend ID
        index = selection[0]
        friend_ids = list(self.friends.keys())
        if index < len(friend_ids):
            return friend_ids[index]
        return None

    def get_selected_pending_id(self):
        """Gets selected pending request ID"""
        selection = self.pending_listbox.curselection()
        if not selection:
            messagebox.showinfo("Info", "Please select a request first")
            return None

        # Get request ID
        index = selection[0]
        pending_ids = list(self.pending_friends.keys())
        if index < len(pending_ids):
            return pending_ids[index]
        return None

    def get_selected_search_id(self):
        """Gets selected search result ID"""
        selection = self.search_listbox.curselection()
        if not selection:
            messagebox.showinfo("Info", "Please select a search result first")
            return None

        # Get result ID
        index = selection[0]
        search_ids = list(self.search_results.keys())
        if index < len(search_ids):
            return search_ids[index]
        return None

    def update_friends_list(self):
        """Updates friends list"""
        self.friends_listbox.delete(0, tk.END)
        for friend in self.friends.values():
            status = "Online" if friend.is_online else "Offline"
            self.friends_listbox.insert(tk.END, f"{friend.username} ({status})")

    def update_pending_list(self):
        """Updates pending requests list"""
        self.pending_listbox.delete(0, tk.END)
        for user in self.pending_friends.values():
            self.pending_listbox.insert(tk.END, f"{user.username} requests to add you as friend")

    def update_search_results(self):
        """Updates search results list"""
        self.search_listbox.delete(0, tk.END)
        for user in self.search_results.values():
            self.search_listbox.insert(tk.END, f"{user.username} ({user.user_id})")

    def update_transfers_list(self):
        """Updates transfers list"""
        # Clear old content
        for item in self.transfers_tree.get_children():
            self.transfers_tree.delete(item)

        # Add new content
        for transfer_id, transfer_info in self.active_transfers.items():
            # Parse transfer information
            filename = transfer_info.get("filename", "Unknown file")
            filesize = self.format_size(transfer_info.get("filesize", 0))
            status = transfer_info.get("status", "Unknown")
            progress = transfer_info.get("progress", 0)

            # Determine peer name
            if "sender_name" in transfer_info:
                peer = f"Receiving from {transfer_info['sender_name']}"
            elif "receiver_name" in transfer_info:
                peer = f"Sending to {transfer_info['receiver_name']}"
            else:
                peer = "Unknown"

            # Status translation
            status_map = {
                "pending": "Pending",
                "connecting": "Connecting",
                "waiting": "Waiting",
                "transferring": "Transferring",
                "completed": "Completed",
                "failed": "Failed",
                "timeout": "Timeout",
                "incomplete": "Incomplete"
            }
            status_text = status_map.get(status, status)

            # Add to treeview
            self.transfers_tree.insert(
                "",
                tk.END,
                values=(filename, filesize, peer, status_text, f"{progress}%")
            )

    def delete_friend(self):
        """Deletes a friend"""
        friend_id = self.get_selected_friend_id()
        if not friend_id:
            return

        friend = self.friends[friend_id]

        # Confirmation dialog
        if not messagebox.askyesno("Confirm", f"Are you sure you want to delete friend {friend.username}?"):
            return

        # Remove from friends list
        del self.friends[friend_id]
        self.save_friends()
        self.update_friends_list()

        messagebox.showinfo("Info", f"Friend {friend.username} deleted")

    def clear_completed_transfers(self):
        """Clears completed transfers"""
        # Find completed transfers
        completed_ids = [
            transfer_id for transfer_id, info in self.active_transfers.items()
            if info.get("status") in ["completed", "failed", "timeout", "incomplete"]
        ]

        # Delete them
        for transfer_id in completed_ids:
            del self.active_transfers[transfer_id]

        # Update list
        self.update_transfers_list()

    def on_closing(self):
        """Handles window close event"""
        # Stop all services
        self.stop_services()

        # Save configuration and friends list
        self.save_config()
        self.save_friends()

        # Close window
        self.root.destroy()

    # Message handling functions
    def handle_discovery_response(self, msg_data, addr):
        """Handles discovery response message"""
        response_id = msg_data.get("user_id")
        response_name = msg_data.get("username")
        query_id = msg_data.get("query_id")

        if query_id == "broadcast" or query_id == response_id:
            # Update friend status or add to search results
            if response_id in self.friends:
                self.friends[response_id].ip = addr[0]
                self.friends[response_id].port = addr[1]
                self.friends[response_id].is_online = True
                self.friends[response_id].last_seen = time.time()
                self.update_friends_list()
                logger.info(f"Friend {response_name} is now online")
            else:
                # Add to search results
                user = User(response_name, response_id, addr[0], addr[1], True)
                self.search_results[response_id] = user
                self.update_search_results()
                logger.info(f"Discovered new user: {response_name}")

    def handle_friend_request(self, msg_data, addr):
        """Handles friend request message"""
        requester_id = msg_data.get("user_id")
        requester_name = msg_data.get("username")

        if requester_id not in self.friends and requester_id != self.user_id:
            user = User(requester_name, requester_id, addr[0], addr[1], True)
            self.pending_friends[requester_id] = user
            self.update_pending_list()
            logger.info(f"Received friend request from {requester_name}")

            # Show notification
            self.root.after(0, lambda: messagebox.showinfo(
                "New Friend Request",
                f"Received friend request from {requester_name}"))

    def handle_friend_response(self, msg_data, addr):
        """Handles friend response message"""
        responder_id = msg_data.get("user_id")
        responder_name = msg_data.get("username")
        accepted = msg_data.get("accepted", False)

        if accepted:
            # Friend request accepted
            if responder_id not in self.friends:
                user = User(responder_name, responder_id, addr[0], addr[1], True)
                self.friends[responder_id] = user
                self.save_friends()
                self.update_friends_list()
                logger.info(f"{responder_name} accepted your friend request")

                # Show notification
                self.root.after(0, lambda: messagebox.showinfo(
                    "Friend Request Accepted",
                    f"{responder_name} has accepted your friend request"))
        else:
            # Friend request rejected
            logger.info(f"{responder_name} rejected your friend request")

            # Show notification
            self.root.after(0, lambda: messagebox.showinfo(
                "Friend Request Rejected",
                f"{responder_name} has rejected your friend request"))

    def handle_file_request(self, msg_data, addr):
        """Handles file request message"""
        sender_id = msg_data.get("user_id")
        sender_name = msg_data.get("username")
        filename = msg_data.get("filename")
        filesize = msg_data.get("filesize")
        transfer_id = msg_data.get("transfer_id")

        if sender_id in self.friends:
            # Update friend status
            self.friends[sender_id].ip = addr[0]
            self.friends[sender_id].port = addr[1]
            self.friends[sender_id].is_online = True
            self.friends[sender_id].last_seen = time.time()

            # Show file transfer request
            self.root.after(0, lambda: self.show_file_request(
                sender_id, sender_name, filename, filesize, transfer_id))

    def handle_file_response(self, msg_data, addr):
        """Handles file response message"""
        transfer_id = msg_data.get("transfer_id")
        accepted = msg_data.get("accepted", False)
        port = msg_data.get("port")

        if transfer_id in self.active_transfers:
            transfer_info = self.active_transfers[transfer_id]
            transfer_info["accepted"] = accepted

            if accepted and port:
                # Start sending file
                transfer_info["remote_port"] = port
                threading.Thread(
                    target=self.send_file,
                    args=(transfer_id,)
                ).start()
            else:
                # File transfer rejected
                self.root.after(0, lambda: messagebox.showinfo(
                    "File Transfer Canceled",
                    f"Your file transfer request was rejected"))
                del self.active_transfers[transfer_id]

    def handle_transfer_complete(self, msg_data, addr):
        """Handles transfer complete message"""
        transfer_id = msg_data.get("transfer_id")
        status = msg_data.get("status")

        if transfer_id in self.active_transfers:
            if status == "completed":
                # Transfer completed successfully
                self.active_transfers[transfer_id]["status"] = "completed"
                self.active_transfers[transfer_id]["progress"] = 100
                self.update_transfers_list()

    def handle_heartbeat(self, msg_data, addr):
        """Handles heartbeat message"""
        sender_id = msg_data.get("user_id")

        if sender_id in self.friends:
            # Update friend status
            self.friends[sender_id].ip = addr[0]
            self.friends[sender_id].port = addr[1]
            self.friends[sender_id].is_online = True
            self.friends[sender_id].last_seen = time.time()
            self.update_friends_list()

    def handle_error(self, msg_data, addr):
        """Handles error message"""
        error_msg = msg_data.get("error", "Unknown error")
        logger.error(f"Received error message: {error_msg}")

    def run(self):
        """Runs the application"""
        self.root.mainloop()


def main():
    """Main function"""
    try:
        app = P2PFileTransfer()
        app.run()
    except Exception as e:
        logging.error(f"Program run error: {e}")
        messagebox.showerror("Error", f"Program run error: {e}")


if __name__ == "__main__":
    main()