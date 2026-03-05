import subprocess
import json
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime

from .logger import get_logger

class DatabaseArchiver:
    """Archives files by marking them as archived in the Immich database"""
    
    def __init__(self, config, hasher):
        self.config = config
        self.hasher = hasher
        self.logger = get_logger('database_archiver')
        
        # API configuration
        self.api_url = self.config.get('immich.api_url')
        self.api_key = self.config.get('immich.api_key')
        
        # Docker path mappings
        self.docker_mappings = self.config.get('immich.docker_path_mappings', {})
        
    def reverse_docker_path_mapping(self, path: str) -> str:
        """Convert host path back to docker container path for database lookup"""
        str_path = str(path)
        
        # Sort mappings by host path length (longest first) for specificity
        sorted_mappings = sorted(self.docker_mappings.items(), 
                               key=lambda x: len(x[1]), reverse=True)
        
        for docker_mount, host_mount in sorted_mappings:
            if str_path.startswith(host_mount):
                mapped_path = str_path.replace(host_mount, docker_mount, 1)
                self.logger.debug(f"Reverse mapped path: {str_path} -> {mapped_path}")
                return mapped_path
        return str_path
    
    def mark_file_as_archived(self, file_path: str) -> bool:
        """Mark a single file as archived using PostgreSQL command line"""
        try:
            # Convert to docker path for database lookup
            docker_path = self.reverse_docker_path_mapping(file_path)
            
            # Use psql to update the database
            query = """
                UPDATE assets 
                SET visibility = 'archive' 
                WHERE "originalPath" = '%s'
                AND visibility != 'archive';
            """ % docker_path.replace("'", "''")  # Escape single quotes
            
            cmd = [
                'docker', 'exec', 'immich_postgres', 
                'psql', '-U', 'postgres', '-d', 'immich', 
                '-c', query
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                # Check if any rows were affected
                if 'UPDATE 1' in result.stdout or 'UPDATE 0' in result.stdout:
                    rows_affected = 1 if 'UPDATE 1' in result.stdout else 0
                    if rows_affected > 0:
                        self.logger.debug(f"Marked as archived: {file_path}")
                        return True
                    else:
                        self.logger.debug(f"File not found in database or already archived: {file_path}")
                        return False
                else:
                    self.logger.debug(f"Marked as archived: {file_path}")
                    return True
            else:
                self.logger.error(f"Database update failed for {file_path}: {result.stderr}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error marking file as archived: {file_path}: {e}")
            return False
    
    def batch_archive(self, file_paths: List[Path]) -> Dict[str, int]:
        """Mark multiple files as archived - matches FileArchiver interface"""
        stats = {
            'total_files': len(file_paths),
            'archived': 0,
            'errors': 0
        }
        
        for file_path in file_paths:
            try:
                if self.mark_file_as_archived(str(file_path)):
                    stats['archived'] += 1
                else:
                    stats['errors'] += 1
            except Exception as e:
                self.logger.error(f"Error archiving {file_path}: {e}")
                stats['errors'] += 1
        
        return stats
    
    def cleanup_archives(self, retention_days: int = None, dry_run: bool = False) -> Dict[str, int]:
        """Clean up old archived files - matches FileArchiver interface"""
        if retention_days is None:
            retention_days = self.config.get('archive.retention_days', 7)
        
        stats = {
            'found': 0,
            'verified_safe': 0,
            'deleted': 0,
            'errors': 0,
            'retention_failures': 0
        }
        
        # Get archived assets older than retention period
        try:
            query = """
                SELECT id, "originalPath" FROM assets 
                WHERE visibility = 'archive'
                AND "createdAt" < NOW() - INTERVAL '%d days';
            """ % retention_days
            
            cmd = [
                'docker', 'exec', 'immich_postgres', 
                'psql', '-U', 'postgres', '-d', 'immich', 
                '-t', '-c', query
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            
            if result.returncode == 0:
                archived_assets = []
                for line in result.stdout.strip().split('\n'):
                    if line.strip() and '|' in line:
                        parts = line.strip().split('|')
                        if len(parts) >= 2:
                            asset_id = parts[0].strip()
                            original_path = parts[1].strip()
                            archived_assets.append((asset_id, original_path))
                
                stats['found'] = len(archived_assets)
                self.logger.info(f"Found {stats['found']} archived assets older than {retention_days} days")
                
                # For each archived asset, verify external copy exists
                for asset_id, original_path in archived_assets:
                    try:
                        # Check if external copy exists in hash index
                        if self.verify_external_copy_exists(original_path):
                            stats['verified_safe'] += 1
                            
                            if not dry_run:
                                # Delete asset via API or database
                                if self.delete_asset_via_api(asset_id):
                                    stats['deleted'] += 1
                                else:
                                    stats['errors'] += 1
                            else:
                                stats['deleted'] += 1
                                self.logger.info(f"Would delete archived asset: {asset_id}")
                        else:
                            stats['retention_failures'] += 1
                            self.logger.warning(f"No verified external copy for asset: {asset_id}")
                            
                    except Exception as e:
                        stats['errors'] += 1
                        self.logger.error(f"Error processing archived asset {asset_id}: {e}")
            else:
                self.logger.error(f"Failed to get archived assets: {result.stderr}")
                
        except Exception as e:
            self.logger.error(f"Error in cleanup operation: {e}")
        
        return stats
    
    def verify_external_copy_exists(self, original_path: str) -> bool:
        """Verify that an external copy exists for the file"""
        try:
            # Use hasher to check if file exists in external library
            file_path = Path(original_path)
            if not file_path.exists():
                # File might be in docker path, try to map it
                mapped_path = self.reverse_docker_path_mapping(original_path)
                file_path = Path(mapped_path)
                if not file_path.exists():
                    return False
            
            # Check if file has been copied to external library
            if self.hasher.is_duplicate(file_path):
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"Error verifying external copy for {original_path}: {e}")
            return False
    
    def delete_asset_via_api(self, asset_id: str) -> bool:
        """Delete asset using Immich API"""
        try:
            import urllib.request
            import urllib.parse
            
            delete_url = f"{self.api_url}/api/assets"
            
            data = json.dumps({
                "ids": [asset_id],
                "force": True
            }).encode('utf-8')
            
            req = urllib.request.Request(delete_url, data=data, method='DELETE')
            req.add_header('Content-Type', 'application/json')
            req.add_header('Accept', 'application/json')
            req.add_header('x-api-key', self.api_key)
            
            with urllib.request.urlopen(req, timeout=30) as response:
                if response.status in [200, 204]:
                    self.logger.info(f"Successfully deleted asset via API: {asset_id}")
                    return True
                else:
                    self.logger.error(f"API delete failed for {asset_id}: status {response.status}")
                    return False
                    
        except Exception as e:
            self.logger.error(f"Error deleting asset {asset_id} via API: {e}")
            return False
    
    def archive_file(self, file_path: Path) -> Optional[Path]:
        """Archive a single file - matches FileArchiver interface"""
        if self.mark_file_as_archived(str(file_path)):
            return file_path  # Return the same path since we're not moving files
        else:
            return None
    
    def get_archive_stats(self) -> Dict[str, int]:
        """Get statistics about archived files - matches FileArchiver interface"""
        stats = {
            'total_archived_files': 0,
            'total_archive_dirs': 0,
            'total_archive_size_mb': 0
        }
        
        try:
            query = "SELECT COUNT(*) FROM assets WHERE visibility = 'archive';"
            
            cmd = [
                'docker', 'exec', 'immich_postgres', 
                'psql', '-U', 'postgres', '-d', 'immich', 
                '-t', '-c', query
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                count = result.stdout.strip()
                if count.isdigit():
                    stats['total_archived_files'] = int(count)
            
            # Note: total_archive_dirs and total_archive_size_mb are not applicable
            # for database archiving since files aren't moved
            
        except Exception as e:
            self.logger.error(f"Error getting archive stats: {e}")
        
        return stats