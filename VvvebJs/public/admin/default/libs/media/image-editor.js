const { TABS, TOOLS } = FilerobotImageEditor;
const config = {
  source: '/media/biglogo.png',
  closeAfterSave:true,
  onSave: (editedImageObject, designState) => {
	console.log('saved', editedImageObject, designState)
	editorContainer.classList.add("d-none");
	currentImage.classList.remove("d-none");
  },
  annotationsCommon: {
    fill: '#333333',
  },
  Text: { 
	  text: 'Vvveb',
	  fontFamily: 'Inter',
	  fontSize: 36,
	  fonts: [
            { label: 'Arial', value: 'Arial' },
            { label: 'Inter', value: 'Inter' },
            { label: 'Inter Variable', value: 'InterVariable' },
            { label: 'Tahoma', value: 'Sans-serif' },
            { label: 'Roboto', value: 'Roboto' },
            { label: 'Helvetica Neue', value: 'Helvetica Neue' },
            { label: 'Noto Sans', value: 'Noto Sans' },
            { label: 'Liberation Sans', value: 'Liberation Sans' },
            { label: 'Segoe UI', value: 'Segoe UI' },
            { label: 'Comic Sans', value: 'Comic-sans' },
      ],
  },
  Rotate: { angle: 90, componentType: 'slider' },
  translations: {
    profile: 'Profile',
    coverPhoto: 'Cover photo',
    facebook: 'Facebook',
    socialMedia: 'Social Media',
    fbProfileSize: '180x180px',
    fbCoverPhotoSize: '820x312px',
  },
  Crop: {
    presetsItems: [
      {
        titleKey: 'classicTv',
        descriptionKey: '4:3',
        ratio: 4 / 3,
        // icon: CropClassicTv, // optional, CropClassicTv is a React Function component. Possible (React Function component, string or HTML Element)
      },
      {
        titleKey: 'cinemascope',
        descriptionKey: '21:9',
        ratio: 21 / 9,
        // icon: CropCinemaScope, // optional, CropCinemaScope is a React Function component.  Possible (React Function component, string or HTML Element)
      },
    ],
    presetsFolders: [
      {
        titleKey: 'socialMedia', // will be translated into Social Media as backend contains this translation key
        // icon: Social, // optional, Social is a React Function component. Possible (React Function component, string or HTML Element)
        groups: [
          {
            titleKey: 'facebook',
            items: [
              {
                titleKey: 'profile',
                width: 180,
                height: 180,
                descriptionKey: 'fbProfileSize',
              },
              {
                titleKey: 'coverPhoto',
                width: 820,
                height: 312,
                descriptionKey: 'fbCoverPhotoSize',
              },
            ],
          },
        ],
      },
    ],
  },
  tabsIds: [ 
	  TABS.ADJUST,
	  TABS.FINETUNE,
	  TABS.FILTERS,
	  TABS.WATERMARK,
	  TABS.ANNOTATE,
	  TABS.RESIZE,
  ], // or ['Adjust', 'Annotate', 'Watermark']
  defaultTabId: TABS.ADJUST, // or 'Annotate'
  defaultToolId: TOOLS.TEXT, // or 'Text'
};

let editorContainer = document.getElementById('editor-container');
let currentImage = document.getElementById("current-image");

// Assuming we have a div with id="editor-container"
const filerobotImageEditor = new FilerobotImageEditor(
  editorContainer,
  config,
);

function DataURIToBlob(dataURI) {
	const splitDataURI = dataURI.split(',')
	const byteString = splitDataURI[0].indexOf('base64') >= 0 ? atob(splitDataURI[1]) : decodeURI(splitDataURI[1])
	const mimeString = splitDataURI[0].split(':')[1].split(';')[0]

	const ia = new Uint8Array(byteString.length)
	for (let i = 0; i < byteString.length; i++)
		ia[i] = byteString.charCodeAt(i)

	return new Blob([ia], { type: mimeString });
}
  
function onSave(imageInfo, designState) {
	console.log('saved', imageInfo, designState)
	editorContainer.classList.add("d-none");
	currentImage.classList.remove("d-none");
	
	const file      = DataURIToBlob(imageInfo.imageBase64);
	const mediaPath = Vvveb.MediaModal.mediaPath + Vvveb.MediaModal.currentPath;
/*	
	let reader = new FileReader();
	reader.onload = imageIsLoaded;
	reader.readAsDataURL(this.files[0]);
	//reader.readAsBinaryString(this.files[0]);
	file = this.files[0];
*/
		
		let formData = new FormData();
		formData.append("file", file, imageInfo.fullName);
		formData.append("mediaPath", mediaPath);
		formData.append("onlyFilename", true);
		formData.append("overwrite", true);
		
		fetch(uploadUrl, {method: "POST",  body: formData})
		.then((response) => {
			if (!response.ok) { return Promise.reject(response); }
			return response.json()
		})
		.then((data) => {
			if (data.success) {
				editorContainer.classList.add("d-none");
				currentImage.classList.remove("d-none");
				//refresh current image
				let img = document.getElementById("current-image");
				img.src = mediaPath + "/" + imageInfo.fullName + "?" + Math.random();
				
				document.getElementById("input-name").value = imageInfo.fullName;
				displayToast("success", "Success", data.message);			
			} else {
				displayToast("danger", "Error", data.message);
			}
		})
		.catch(error => {
			let message = error.statusText ?? "Error uploading!";
			Vvveb.MediaModal.hideUploadLoading();						
			displayToast("danger", "Error", message);
		});	
return;	
/*	
  const url = imageInfo.imageBase64;
  const { fullName: fileName } = imageInfo;

  let tmpLink = document.createElement('a');
  tmpLink.href = url;

  tmpLink.download = fileName;

  tmpLink.style = 'position: absolute; z-index: -111; visibility: none;';
  document.body.appendChild(tmpLink);
  tmpLink.click();
  document.body.removeChild(tmpLink);
  tmpLink = null;
*/  
}


const base64toBlob = (base64url) => fetch(base64url).then((res) => res.blob());

function onSaveToClipboard(imageInfo) {
  // create blob from base64 string
  const blob = base64toBlob(imageInfo.imageBase64, imageInfo.mimeType);
  // eslint-disable-next-line prettier/prettier
  navigator.clipboard.write([
      new ClipboardItem({
        [imageInfo.mimeType]: blob,
      }),
    ])
    .then(() => {
      // eslint-disable-next-line no-undef
      Toastify({
        text: 'Image copied to clipboard',
        duration: 5000,
      }).showToast();
    })
    .catch((error) => {
      // eslint-disable-next-line no-console
      console.error(error);
      // eslint-disable-next-line no-undef
      Toastify({
        text: 'Failed to copy image, see consle for error',
        duration: 5000,
      }).showToast();
    });
}


document.getElementById("edit-image-btn").addEventListener('click', function (e) {
	filerobotImageEditor.config.source = currentImage.src + "?" + Math.random();

	filerobotImageEditor.render({
	  onSave,/*
	  moreSaveOptions: [
		{
		  label: 'Save with Options',
		  id: 'dialog',
		  //icon: SettingsIcon,
		  onClick: (openSaveModal) => openSaveModal(onSave),
		},
		{
		  label: 'Download (JPG)',
		  id: 'downloadJpg',
		  //icon: DownloadIcon,
		  onClick: (_openSaveModal, saveDirectly) => saveDirectly(onSave, 'jpg'),
		},
		{
		  label: 'Download (PNG)',
		  id: 'downloadPng',
		  //icon: DownloadIcon,
		  onClick: (_openSaveModal, saveDirectly) => saveDirectly(onSave, 'png'),
		},
		{
		  label: 'Download (WEBP)',
		  id: 'downloadWebp',
		  //icon: DownloadIcon,
		  onClick: (_openSaveModal, saveDirectly) => saveDirectly(onSave, 'webp'),
		},
		{
		  label: 'Save To Clipboard (PNG)',
		  id: 'copyPng',
		  //icon: DuplicateIcon,
		  onClick: (_openSaveModal, saveDirectly) =>
			saveDirectly(onSaveToClipboard, 'png'),
		},
	  ],*/
	  avoidChangesNotSavedAlertOnLeave: true,
	  disableSaveIfNoChanges: true,
	  useCloudimage: false,
	  onClose: (closingReason) => {
		editorContainer.classList.add("d-none");
		currentImage.classList.remove("d-none");

		console.log('Closing reason', closingReason);
		filerobotImageEditor.terminate();
	  },
	});

	editorContainer.classList.remove("d-none");
	currentImage.classList.add("d-none");

	//filerobotImageEditor.render();

});	
