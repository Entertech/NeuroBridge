#include "DSPEEG.h"
#include "Filtfilt.h"
#include "Wavelet.hpp"
#include "MathTool.h"
#include "FFT.hpp"

namespace basic
{
    namespace dsp
    {
        static vectord eegDriftFilterParamsX = {0.95097189, -2.85291566, 2.85291566, -0.95097189};
        static vectord eegDriftFilterParamsY = {1.0, -2.89947959, 2.80394798, -0.90434753};
        static vectord eegLowpassFilterParamsX = {0.06508716, 0.19526149, 0.19526149, 0.06508716};
        static vectord eegLowpassFilterParamsY = {1.0, -0.9501487, 0.57455598, -0.10370997};
        static vectord eegHighpassFilterParamsX = {0.9408093, -0.9408093};
        static vectord eegHighpassFilterParamsY = {1.0, -0.88161859};
        static vectord eegPf50NotchFilterParamsX = {0.97948276, -0.60535364, 0.97948276};
        static vectord eegPf50NotchFilterParamsY = {1., -0.60535364, 0.95896552};
        static vectord eegPf60NotchFilterParamsX = {0.97547839, -0.12250159, 0.97547839};
        static vectord eegPf60NotchFilterParamsY = {1., -0.12250159, 0.95095678};

        vectord eegDriftFilter(vectord inputWave)
        {
            auto inputMean = mathtool::mean(inputWave);

            for (auto &e : inputWave)
            {
                e = e - inputMean;
            }

            vectord outputWave;

            mathtool::filtfilt(eegDriftFilterParamsX, eegDriftFilterParamsY, inputWave, outputWave);

            return outputWave;
        }

        vectord eegPfNotch(vectord &inputWave)
        {
            vectord tempWave;
            mathtool::filtfilt(eegPf50NotchFilterParamsX, eegPf50NotchFilterParamsY, inputWave, tempWave);
            vectord outputWave;
            mathtool::filtfilt(eegPf60NotchFilterParamsX, eegPf60NotchFilterParamsY, tempWave, outputWave);
            return outputWave;
        }

        vectord eegLowpassFilter(vectord &inputWave)
        {
            vectord tempWave;
            mathtool::filtfilt(eegLowpassFilterParamsX, eegLowpassFilterParamsY, inputWave, tempWave);
            return tempWave;
        }

        vectord eegHighpassFilter(vectord &inputWave)
        {
            vectord tempWave;
            mathtool::filtfilt(eegHighpassFilterParamsX, eegHighpassFilterParamsY, inputWave, tempWave);
            return tempWave;
        }

        std::pair<vectord, vectord> eegArtifactRemove(vectord &inputWave, const std::string &waveletName)
        {
            //小波分解

            auto res = mathtool::wavedec(inputWave, waveletName, 6);

            // 根据各层细节系数保留个数，将低于阈值部分置零（Birge-Massart自适应策略）
            auto ca6 = res[0];
            std::vector<std::vector<double>> cdList, cf;
            cdList.assign(res.cbegin() + 1, res.cend());
            cf.push_back(res[0]);
            for (size_t i = 0; i < cdList.size(); i++)
            {
                auto cdi = cdList[i];
                auto hold_num = static_cast<int>(std::round(1.55 * ca6.size() / std::pow(8. - (6 - i), 2)));
                
                auto cdSort = mathtool::sort(mathtool::abs(cdi));
                auto cdThr = mathtool::truncate(cdSort, -hold_num);
                
                for (auto &&item : cdi)
                    if (std::abs(item) < cdThr + 0.0001)
                        item = 0.;
                cf.emplace_back(cdi);
            }
            //小波重构
            auto artifact = mathtool::waverec(cf, waveletName);

            while (artifact.size() > inputWave.size())
            {
                artifact.pop_back();
            }


            auto outputWave = mathtool::minus(inputWave, artifact);

            return std::make_pair(outputWave, artifact);
        }

        vectord eegWaveletDenoise(vectord &inputWave,
                                  const std::string &waveletName,
                                  const std::string &thresholdMode)
        {
            auto res = mathtool::wavedec(inputWave, waveletName, 5);
            int ca5Thr = 131;
            int cd5Thr = 204;
            int cd4Thr = 211;
            int cd3Thr = 155;
            int cd2Thr = 72;
            int cd1Thr = 8;
            if (thresholdMode == "soft")
            {
                mathtool::softThreshold(res[0], ca5Thr, 5);
                mathtool::softThreshold(res[1], cd5Thr, 5);
                mathtool::softThreshold(res[2], cd4Thr, 5);
                mathtool::softThreshold(res[3], cd3Thr, 5);
                mathtool::softThreshold(res[4], cd2Thr, 5);
                mathtool::softThreshold(res[5], cd1Thr, 5);
            }
            else if (thresholdMode == "hard")
            {
                mathtool::hardThreshold(res[0], ca5Thr);
                mathtool::hardThreshold(res[1], cd5Thr);
                mathtool::hardThreshold(res[2], cd4Thr);
                mathtool::hardThreshold(res[3], cd3Thr);
                mathtool::hardThreshold(res[4], cd2Thr);
                mathtool::hardThreshold(res[5], cd1Thr);
            }
            else
            {
                throw std::invalid_argument("Undefined threshold mode!");
            }

            auto eegWave = mathtool::waverec(res, waveletName);

            return eegWave;
        }

        EEGPower eegPowerCal(vectord &inputWave, DeviceInfo *deviceInfo)
        {
            auto eegPower = EEGPower();
            auto ncWave = nc::NdArray(inputWave);
            auto waveHam = nc::multiply(ncWave, nc::hamming(ncWave.size())).toStlVector();
            auto fftWave = mathtool::fft(waveHam, waveHam.size());
            auto absWave = mathtool::abs(fftWave);
            auto ncAbsWave = nc::NdArray(absWave);
            auto spec = nc::power(ncAbsWave, 2).toStlVector();
            // 脑电频段能量计算
            auto waveHamSize = waveHam.size();
            auto powerTruncate = mathtool::truncate(spec, 0, static_cast<int>(waveHamSize * 45 / deviceInfo->eegFs()) + 1);
            eegPower.power = mathtool::sum(powerTruncate);
            eegPower.alpha = mathtool::sum(mathtool::truncate(spec, static_cast<int>(waveHamSize * 8 / deviceInfo->eegFs()), static_cast<int>(waveHamSize * 13 / deviceInfo->eegFs()) + 1));
            eegPower.beta = mathtool::sum(mathtool::truncate(spec, static_cast<int>(waveHamSize * 14 / deviceInfo->eegFs()), static_cast<int>(waveHamSize * 28 / deviceInfo->eegFs()) + 1));
            eegPower.theta = mathtool::sum(mathtool::truncate(spec, static_cast<int>(waveHamSize * 4 / deviceInfo->eegFs()), static_cast<int>(waveHamSize * 7 / deviceInfo->eegFs()) + 1));
            eegPower.delta = mathtool::sum(mathtool::truncate(spec, static_cast<int>(waveHamSize * 0.5 / deviceInfo->eegFs()), static_cast<int>(waveHamSize * 4 / deviceInfo->eegFs()) + 1));
            eegPower.gamma = mathtool::sum(mathtool::truncate(spec, static_cast<int>(waveHamSize * 30 / deviceInfo->eegFs()), static_cast<int>(waveHamSize * 45 / deviceInfo->eegFs()) + 1));
            eegPower.highBeta = mathtool::sum(mathtool::truncate(spec, static_cast<int>(waveHamSize * 21 / deviceInfo->eegFs()), static_cast<int>(waveHamSize * 28 / deviceInfo->eegFs()) + 1));
            eegPower.lowBeta = mathtool::sum(mathtool::truncate(spec, static_cast<int>(waveHamSize * 16 / deviceInfo->eegFs()), static_cast<int>(waveHamSize * 20 / deviceInfo->eegFs()) + 1));
            return eegPower;
        }

        double eegSnrCal(vectord &inputWave, DeviceInfo *deviceInfo)
        {
            auto ncWave = nc::NdArray(inputWave);
            auto waveHam = nc::multiply(ncWave, nc::hamming(ncWave.size())).toStlVector();
            auto fftWave = mathtool::fft(waveHam, waveHam.size());
            auto absWave = mathtool::abs(fftWave);
            auto ncAbsWave = nc::NdArray(absWave);
            auto spec = nc::power(ncAbsWave, 2).toStlVector();
            // 脑电频段能量计算
            auto waveHamSize = waveHam.size();
            auto infoPower = mathtool::sum(mathtool::truncate(spec, static_cast<int>(waveHamSize * 30 / deviceInfo->eegFs()), static_cast<int>(waveHamSize * 45 / deviceInfo->eegFs()) + 1));
            auto noisePower50 = mathtool::sum(mathtool::truncate(spec, static_cast<int>(waveHamSize * 49 / deviceInfo->eegFs()), static_cast<int>(waveHamSize * 51 / deviceInfo->eegFs()) + 1));
            auto noisePower60 = mathtool::sum(mathtool::truncate(spec, static_cast<int>(waveHamSize * 59 / deviceInfo->eegFs()), static_cast<int>(waveHamSize * 61 / deviceInfo->eegFs()) + 1));
            auto noisePower = std::max(noisePower50, noisePower60);
            auto snr = std::log(infoPower/(noisePower + 0.00001));
            return snr;
        }

        EEGQuality eegQualityCal(vectord filterWave,
                                 DeviceInfo *deviceInfo,
                                 bool waveAmplified)
        {
            EEGQuality quality;
            // 信号参数计算
            if (!waveAmplified)
            {
                for (auto &e : filterWave)
                {
                    e = e*deviceInfo->eegVoltageGain();
                }
            }
            auto snr = eegSnrCal(filterWave, deviceInfo);
            nc::NdArray ncWave(filterWave);

            auto amp = nc::max(nc::abs(ncWave)).at(0);
            auto ncWaveSum = nc::sum(nc::multiply(ncWave, ncWave)).at(0);
            auto rms = nc::sqrt(ncWaveSum / ncWave.size());

            if (snr < -1.66)
            {
                quality = NONE;
            }
            else if (nc::log(amp) < 5.75 && nc::log(rms) < 4.46)
            {
                quality = GOOD;
            }
            else if (nc::log(amp) < 6.12 && nc::log(rms) < 4.6)
            {
                quality = GOOD;
            } else 
            {
                quality = POOR;
            }
            return quality;
            
        }
    }
}