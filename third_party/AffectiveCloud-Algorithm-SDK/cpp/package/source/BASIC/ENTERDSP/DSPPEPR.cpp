//
// Created by Enter M1 on 2023/8/28.
//

#include "DSPPEPR.h"
#include "Wavelet.hpp"
#include <algorithm>
#include <cmath>
#include <numeric>

namespace basic::dsp
{
    /// 呼吸波去噪
    /// \param inputWave 呼吸波
    /// \return 去噪后的呼吸波
    vectord rwWaveDenoise(const vectord& inputWave)
    {
        auto res = mathtool::wavedec(inputWave, "sym5", 6);
        vectord denoiseWave;
        if (res.size() == 7)
        {
            for (int i = 1; i < 6; ++i) {
                std::fill(res[i].begin(), res[i].end(), 0.);
            }
            denoiseWave = mathtool::waverec(res, "sym5");
            if (denoiseWave.size() > inputWave.size())
                denoiseWave.resize(inputWave.size());
        }

        return denoiseWave;
    }

    /// 脉搏波信号质量检测
    /// \param bcgOutputWave 输出脉搏波波形
    /// \param oriWave 原始波形
    /// \param bcgAmpThr 脉搏波幅值阈值
    /// \param oriWaveRangeThr 原始波形跨度阈值
    /// \return 信号质量
    BCGQuality bcgQualityCal(const std::vector<double>& bcgOutputWave, const std::vector<double>& oriWave, double bcgAmpThr, double oriWaveRangeThr) {
        int waveLen = oriWave.size();
        double bcgOutputAmp = *std::max_element(bcgOutputWave.begin(), bcgOutputWave.end(), [](double a, double b) {
            return std::abs(a) < std::abs(b);
        });
        bcgOutputAmp = abs(bcgOutputAmp);
        double oriWaveRange = *std::max_element(oriWave.begin(), oriWave.end()) - *std::min_element(oriWave.begin(), oriWave.end());
        int count = std::count_if(oriWave.begin(), oriWave.end(), [](double value) {
            return value >= 2750 || value <= 30;
        });
        if (count > waveLen * 0.3) {
            return BCGQuality::BCG_NONE;
        }
        if (bcgOutputAmp > 200 || bcgOutputAmp > bcgAmpThr || oriWaveRange > 1000 || oriWaveRange > oriWaveRangeThr) {
            return BCGQuality::BCG_POOR;
        }
        else if (bcgOutputAmp < 5) {
            return BCGQuality::BCG_POOR;
        }
        else {
            return BCGQuality::BCG_NORM;
        }
    }

    /// 呼吸波信号质量检测
    /// \param rwWave 呼吸波波形
    /// \param oriWave 原始波形
    /// \param rwRangeThr 呼吸波跨度阈值
    /// \return
    RWQuality rwQualityCal(std::vector<double> rwWave, std::vector<double> oriWave, double rwRangeThr) {

        double rwWaveMean = std::accumulate(rwWave.begin(), rwWave.end(), 0.0) / rwWave.size();
        double oriWaveMean = std::accumulate(oriWave.begin(), oriWave.end(), 0.0) / oriWave.size();
        for (auto& value : rwWave) {
            value -= rwWaveMean;
        }
        for (auto& value : oriWave) {
            value -= oriWaveMean;
        }
        double rwAmp = *std::max_element(rwWave.begin(), rwWave.end(), [](double a, double b) {
            return std::abs(a) < std::abs(b);
        });
        rwAmp = std::abs(rwAmp);
        double oriRange = *std::max_element(oriWave.begin(), oriWave.end()) - *std::min_element(oriWave.begin(), oriWave.end());
        if (rwAmp == 0) {
            return RWQuality::RW_NONE;
        }
        if (rwAmp > 400 || oriRange > rwRangeThr || oriRange > 1000) {
            return RWQuality::RW_POOR;
        } else if (rwAmp < 5 || oriRange < 25) {
            return RWQuality::RW_POOR;
        } else {
            return RWQuality::RW_NORM;
        }
    }

}