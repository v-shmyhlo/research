import numpy as np
import torch
from torch.nn import functional as F


def pad_and_pack(tensors):
    sizes = [t.shape[0] for t in tensors]

    tensor = torch.zeros(
        len(sizes), max(sizes), dtype=tensors[0].dtype, layout=tensors[0].layout, device=tensors[0].device)
    mask = torch.zeros(
        len(sizes), max(sizes), dtype=torch.bool, layout=tensors[0].layout, device=tensors[0].device)

    for i, t in enumerate(tensors):
        tensor[i, :t.size(0)] = t
        mask[i, :t.size(0)] = True

    return tensor, mask


def collate_fn(batch):
    sigs, syms = list(zip(*batch))

    sigs, sigs_mask = pad_and_pack(sigs)
    syms, syms_mask = pad_and_pack(syms)

    return (sigs, syms), (sigs_mask, syms_mask)


# TODO:
def griffin_lim(spectra, spectra_module, n_iters=30):
    angles = np.angle(np.exp(2j * np.pi * np.random.rand(spectra.size(0), 552, spectra.size(2))))
    angles = torch.tensor(angles, dtype=torch.float, device=spectra.device)
    audio = spectra_module.spectra_to_wave(spectra, angles).squeeze(1)

    for i in range(n_iters):
        _, angles = spectra_module.wave_to_spectra(audio)
        audio = spectra_module.spectra_to_wave(spectra, angles).squeeze(1)

    return audio


def downsample_mask(input, size):
    assert input.dim() == 2
    assert input.dtype == torch.bool

    input = input.unsqueeze(1).float()
    input = F.interpolate(input, size=size, mode='nearest')
    input = input.squeeze(1).bool()

    return input