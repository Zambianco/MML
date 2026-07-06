from django import forms

from .models import MonitoredDirectory


class MonitoredDirectoryForm(forms.ModelForm):
    class Meta:
        model = MonitoredDirectory
        fields = ["name", "path", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Minha biblioteca"}),
            "path": forms.TextInput(attrs={"class": "form-control", "placeholder": "/music ou C:\\Music"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
