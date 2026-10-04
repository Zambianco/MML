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


class ManifestUploadForm(forms.Form):
    file = forms.FileField(
        label="Arquivo do manifesto",
        required=False,
        widget=forms.FileInput(attrs={"class": "form-control"}),
    )
    manifest_text = forms.CharField(
        label="Ou cole os hashes",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control font-monospace", "rows": 8}),
    )

    def clean(self):
        cleaned = super().clean()
        uploaded_file = cleaned.get("file")
        text = cleaned.get("manifest_text") or ""
        if uploaded_file:
            try:
                text = uploaded_file.read().decode("utf-8-sig")
            except UnicodeDecodeError:
                raise forms.ValidationError("Nao foi possivel ler o arquivo em UTF-8.")
        if not text.strip():
            raise forms.ValidationError("Envie um arquivo ou cole os hashes do manifesto.")
        cleaned["manifest_text"] = text
        return cleaned
