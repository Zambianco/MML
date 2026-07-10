from django import forms

from .models import BackupTarget, MonitoredDirectory


class BackupReconciliationForm(forms.Form):
    manifest_text = forms.CharField(
        label="Manifesto do backup",
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 6, "placeholder": "Cole aqui uma lista de caminhos ou sha256, um por linha"}),
    )


class MonitoredDirectoryForm(forms.ModelForm):
    class Meta:
        model = MonitoredDirectory
        fields = ["name", "path", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Minha biblioteca"}),
            "path": forms.TextInput(attrs={"class": "form-control", "placeholder": "/music ou C:\\Music"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }


class BackupTargetForm(forms.ModelForm):
    class Meta:
        model = BackupTarget
        fields = [
            "name",
            "backend_type",
            "is_active",
            "is_default",
            "base_path",
            "aws_bucket",
            "aws_region",
            "aws_access_key_id",
            "aws_secret_access_key",
            "aws_endpoint_url",
            "aws_prefix",
            "sftp_host",
            "sftp_port",
            "sftp_username",
            "sftp_password",
            "sftp_private_key",
            "sftp_remote_path",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Backup principal"}),
            "backend_type": forms.Select(attrs={"class": "form-select", "data-backup-backend": "true"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "is_default": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "base_path": forms.TextInput(attrs={"class": "form-control", "placeholder": "flac/ ou /mnt/archive"}),
            "aws_bucket": forms.TextInput(attrs={"class": "form-control", "placeholder": "meu-bucket"}),
            "aws_region": forms.TextInput(attrs={"class": "form-control", "placeholder": "us-east-1"}),
            "aws_access_key_id": forms.TextInput(attrs={"class": "form-control"}),
            "aws_secret_access_key": forms.PasswordInput(attrs={"class": "form-control"}, render_value=True),
            "aws_endpoint_url": forms.TextInput(attrs={"class": "form-control", "placeholder": "https://s3.amazonaws.com"}),
            "aws_prefix": forms.TextInput(attrs={"class": "form-control", "placeholder": "originais/"}),
            "sftp_host": forms.TextInput(attrs={"class": "form-control", "placeholder": "meu-pc.dyndns.org"}),
            "sftp_port": forms.NumberInput(attrs={"class": "form-control", "placeholder": "22"}),
            "sftp_username": forms.TextInput(attrs={"class": "form-control"}),
            "sftp_password": forms.PasswordInput(attrs={"class": "form-control"}, render_value=True),
            "sftp_private_key": forms.Textarea(attrs={"class": "form-control", "rows": 4, "placeholder": "-----BEGIN OPENSSH PRIVATE KEY-----"}),
            "sftp_remote_path": forms.TextInput(attrs={"class": "form-control", "placeholder": "/backup/music"}),
        }