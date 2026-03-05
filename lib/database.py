#!/usr/bin/env python3
"""
Immich database operations module.
Handles parsing of Immich database backups and extracting asset information.
"""

import gzip
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .logger import get_logger


class ImmichDatabase:
    """Immich database operations and backup parsing."""
    
    def __init__(self, config):
        self.config = config
        self.logger = get_logger('database')
    
    def get_latest_backup(self) -> Optional[Path]:
        """Find the most recent Immich database backup file."""
        backup_dir = Path(self.config.get('immich.backup_dir'))
        
        if not backup_dir.exists():
            self.logger.error(f"Backup directory does not exist: {backup_dir}")
            return None
        
        # Look for .sql.gz files and .sql files
        backup_files = []
        backup_files.extend(backup_dir.glob('*.sql.gz'))
        backup_files.extend(backup_dir.glob('*.sql'))
        
        if not backup_files:
            self.logger.warning(f"No backup files found in {backup_dir}")
            return None
        
        # Sort by modification time and get the latest
        latest_backup = max(backup_files, key=lambda f: f.stat().st_mtime)
        self.logger.info(f"Using latest backup: {latest_backup}")
        
        return latest_backup
    
    def parse_assets(self, backup_file: Path = None) -> List[Dict]:
        """Extract asset records from database backup."""
        if backup_file is None:
            backup_file = self.get_latest_backup()
            if backup_file is None:
                return []
        
        assets = self.extract_sql_data('asset', backup_file)
        self.logger.info(f"Found {len(assets)} assets in database")
        return assets
    
    def parse_users(self, backup_file: Path = None) -> List[Dict]:
        """Extract user records from database backup."""
        if backup_file is None:
            backup_file = self.get_latest_backup()
            if backup_file is None:
                return []
        
        users = self.extract_sql_data('user', backup_file)
        self.logger.info(f"Found {len(users)} users in database")
        return users
    
    def parse_albums(self, backup_file: Path = None) -> List[Dict]:
        """Extract album records from database backup."""
        if backup_file is None:
            backup_file = self.get_latest_backup()
            if backup_file is None:
                return []
        
        albums = self.extract_sql_data('album', backup_file)
        self.logger.info(f"Found {len(albums)} albums in database")
        return albums
    
    def parse_album_assets(self, backup_file: Path = None) -> List[Dict]:
        """Extract album-asset relationship records from database backup."""
        if backup_file is None:
            backup_file = self.get_latest_backup()
            if backup_file is None:
                return []
        
        # This table links albums to assets
        album_assets = self.extract_sql_data('album_asset', backup_file)
        self.logger.info(f"Found {len(album_assets)} album-asset relationships in database")
        return album_assets
    
    def extract_sql_data(self, table_name: str, backup_file: Path) -> List[Dict]:
        """Extract data from a specific table in the database backup."""
        try:
            # Read the backup file (handle both compressed and uncompressed)
            if backup_file.suffix == '.gz':
                with gzip.open(backup_file, 'rt', encoding='utf-8') as f:
                    content = f.read()
            else:
                with open(backup_file, 'r', encoding='utf-8') as f:
                    content = f.read()
            
            # Find COPY statements for the table
            copy_pattern = rf'COPY public\."{table_name}" \([^)]+\) FROM stdin;'
            copy_match = re.search(copy_pattern, content)
            if not copy_match:
                copy_pattern = rf"COPY public\.{table_name} \([^)]+\) FROM stdin;"
                copy_match = re.search(copy_pattern, content)
            
            if not copy_match:
                self.logger.warning(f"Table {table_name} not found in backup")
                return []
            
            # Extract column names
            columns_match = re.search(rf'COPY public\."{table_name}" \(([^)]+)\)', content)
            if not columns_match:
                columns_match = re.search(rf"COPY public\.{table_name} \(([^)]+)\)", content)
            
            if columns_match:
                column_names = [col.strip().strip('"') for col in columns_match.group(1).split(',')]
            else:
                self.logger.error(f"Could not extract column names for table {table_name}")
                return []
            
            # Find the data section
            start_pos = copy_match.end()
            end_pattern = r"^\\\.$"
            end_match = re.search(end_pattern, content[start_pos:], re.MULTILINE)
            
            if not end_match:
                self.logger.error(f"Could not find end of data for table {table_name}")
                return []
            
            # Extract and parse the data lines
            data_section = content[start_pos:start_pos + end_match.start()]
            data_lines = [line.strip() for line in data_section.strip().split('\n') if line.strip()]
            
            # Parse each line into a dictionary
            records = []
            for line_num, line in enumerate(data_lines, 1):
                try:
                    values = self._parse_postgres_line(line)
                    if len(values) == len(column_names):
                        record = dict(zip(column_names, values))
                        records.append(record)
                    else:
                        self.logger.warning(
                            f"Column count mismatch on line {line_num} of table {table_name}: "
                            f"expected {len(column_names)}, got {len(values)}"
                        )
                except Exception as e:
                    self.logger.error(f"Error parsing line {line_num} of table {table_name}: {e}")
                    continue
            
            self.logger.info(f"Successfully parsed {len(records)} records from table {table_name}")
            return records
            
        except Exception as e:
            self.logger.error(f"Error extracting data from table {table_name}: {e}")
            return []
    
    def _parse_postgres_line(self, line: str) -> List[str]:
        """Parse a PostgreSQL COPY data line, handling tabs and escape sequences."""
        # Split on tabs and handle PostgreSQL escape sequences
        values = []
        current_value = ""
        i = 0
        
        while i < len(line):
            char = line[i]
            
            if char == '\t':
                values.append(self._unescape_postgres_value(current_value))
                current_value = ""
            elif char == '\\' and i + 1 < len(line):
                next_char = line[i + 1]
                if next_char == 'N':
                    # \N represents NULL
                    current_value += '\\N'
                    i += 1
                elif next_char == 't':
                    current_value += '\t'
                    i += 1
                elif next_char == 'n':
                    current_value += '\n'
                    i += 1
                elif next_char == 'r':
                    current_value += '\r'
                    i += 1
                elif next_char == '\\':
                    current_value += '\\'
                    i += 1
                else:
                    current_value += char
            else:
                current_value += char
            
            i += 1
        
        # Add the last value
        values.append(self._unescape_postgres_value(current_value))
        
        return values
    
    def _unescape_postgres_value(self, value: str) -> Optional[str]:
        """Convert PostgreSQL escaped value to Python value."""
        if value == '\\N':
            return None
        
        # Handle other escape sequences
        value = value.replace('\\t', '\t')
        value = value.replace('\\n', '\n')
        value = value.replace('\\r', '\r')
        value = value.replace('\\\\', '\\')
        
        return value
    
    def get_file_path(self, asset: Dict, users: List[Dict] = None) -> Optional[Path]:
        """Construct file path from asset record."""
        try:
            # Extract necessary fields from asset
            user_id = asset.get('ownerId')
            original_path = asset.get('originalPath')
            
            if not user_id or not original_path:
                self.logger.warning(f"Missing required fields for asset {asset.get('id', 'unknown')}")
                return None
            
            # Convert Docker path to host path
            host_path = self._convert_docker_path_to_host(original_path)
            
            if not host_path.exists():
                self.logger.warning(f"Asset file does not exist: {host_path}")
                return None
            
            return host_path
            
        except Exception as e:
            self.logger.error(f"Error constructing file path for asset {asset.get('id', 'unknown')}: {e}")
            return None
    
    def _convert_docker_path_to_host(self, docker_path: str) -> Path:
        """Convert Docker container path to host path using path mappings."""
        path_mappings = self.config.get('immich.docker_path_mappings', {})
        
        # Try each mapping to find a match
        for docker_prefix, host_prefix in path_mappings.items():
            if docker_path.startswith(docker_prefix):
                # Replace the Docker prefix with host prefix
                relative_path = docker_path[len(docker_prefix):].lstrip('/')
                host_path = Path(host_prefix) / relative_path
                return host_path
        
        # If no mapping found, assume it's already a host path or try default behavior
        self.logger.warning(f"No Docker path mapping found for: {docker_path}")
        
        # Fall back to original behavior for legacy compatibility
        immich_dir = Path(self.config.get('immich.data_dir'))
        upload_dir = immich_dir / "upload"
        
        # If path looks like it starts with user ID, assume it's relative to upload dir
        path_parts = docker_path.strip('/').split('/')
        if len(path_parts) >= 2:
            # Assume format: user_id/rest/of/path
            return upload_dir / '/'.join(path_parts)
        
        # Last resort: treat as absolute path
        return Path(docker_path)
    
    def get_user_by_id(self, user_id: str, users: List[Dict]) -> Optional[Dict]:
        """Find user record by ID."""
        for user in users:
            if user.get('id') == user_id:
                return user
        return None
    
    def get_album_by_id(self, album_id: str, albums: List[Dict]) -> Optional[Dict]:
        """Find album record by ID."""
        for album in albums:
            if album.get('id') == album_id:
                return album
        return None
    
    def get_albums_by_user(self, user_id: str, albums: List[Dict]) -> List[Dict]:
        """Get all albums owned by a specific user."""
        user_albums = []
        for album in albums:
            if album.get('ownerId') == user_id:
                user_albums.append(album)
        return user_albums
    
    def get_assets_in_album(self, album_id: str, album_assets: List[Dict], assets: List[Dict]) -> List[Dict]:
        """Get all assets that belong to a specific album."""
        # Get asset IDs from the album-assets relationship table
        asset_ids_in_album = set()
        for album_asset in album_assets:
            if album_asset.get('albumsId') == album_id:
                asset_ids_in_album.add(album_asset.get('assetsId'))
        
        # Filter assets to only include those in the album
        album_assets_list = []
        for asset in assets:
            if asset.get('id') in asset_ids_in_album:
                album_assets_list.append(asset)
        
        return album_assets_list
    
    def filter_image_assets(self, assets: List[Dict]) -> List[Dict]:
        """Filter assets to only include image and video files."""
        image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif', '.webp', '.heic', '.raw'}
        video_extensions = {'.mp4', '.avi', '.mov', '.mkv', '.wmv', '.m4v', '.mpg', '.mpeg', '.flv'}
        
        filtered_assets = []
        for asset in assets:
            original_path = asset.get('originalPath', '')
            if original_path:
                ext = Path(original_path).suffix.lower()
                if ext in image_extensions or ext in video_extensions:
                    filtered_assets.append(asset)
        
        self.logger.info(f"Filtered to {len(filtered_assets)} image/video assets from {len(assets)} total")
        return filtered_assets
    
    def filter_non_external_library_assets(self, assets: List[Dict]) -> List[Dict]:
        """Filter out assets that are from external libraries."""
        filtered_assets = []
        external_count = 0
        
        for asset in assets:
            # Check if asset is from external library
            # Note: PostgreSQL boolean values come as 't'/'f' strings, not Python booleans
            is_external_raw = asset.get('isExternal', False)
            is_external = is_external_raw in (True, 't', 'true', '1', 1)
            library_id = asset.get('libraryId')
            
            # Skip external library assets (assets with libraryId or isExternal=true)
            if is_external or library_id:
                external_count += 1
                continue
            
            filtered_assets.append(asset)
        
        self.logger.info(f"Filtered out {external_count} external library assets, {len(filtered_assets)} remaining")
        return filtered_assets
    
    def get_asset_metadata(self, asset: Dict) -> Dict:
        """Extract useful metadata from asset record."""
        return {
            'id': asset.get('id'),
            'user_id': asset.get('ownerId'),
            'original_path': asset.get('originalPath'),
            'original_filename': asset.get('originalFileName'),
            'file_created_at': asset.get('fileCreatedAt'),
            'file_modified_at': asset.get('fileModifiedAt'),
            'created_at': asset.get('createdAt'),
            'type': asset.get('type'),
            'is_favorite': asset.get('isFavorite'),
            'is_archived': asset.get('isArchived'),
            'is_trashed': asset.get('isTrashed', False)
        }