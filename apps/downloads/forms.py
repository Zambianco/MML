from django import forms


class TrackImportUploadForm(forms.Form):
    file = forms.FileField(
        label="Arquivo CSV",
        widget=forms.FileInput(attrs={"class": "form-control", "accept": ".csv,text/csv"}),
    )

    def clean_file(self):
        uploaded_file = self.cleaned_data["file"]
        if not uploaded_file.name.lower().endswith(".csv"):
            raise forms.ValidationError("Envie um arquivo CSV.")
        return uploaded_file
