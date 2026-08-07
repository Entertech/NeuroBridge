#include "EEGHandler.h"
#include "Pretreat.h"
#include "NumCpp.hpp"
#include "DSPEEG.h"

namespace basic
{
    namespace dsp
    {
        namespace eeghandler
        {

            EEGHandlerResult handler(const vectord &eegRawData, double splitLen, double epochLen,
                                     DeviceInfo *deviceInfo, EEGHandlerTemp &tmp)
            {
                auto eegFs = deviceInfo->eegFs();
                auto dataLen = static_cast<size_t>(std::round(splitLen * eegFs)); // 数据切片长度（数据点数）
                auto stepLen = eegRawData.size();                                 // 数据步长（数据点数

                EEGHandlerResult res; //输出结果
                res.quality = NONE;
                EEGPower powerTmp;
                res.power = powerTmp;
                EEGPower tk;
                res.featurePower = tk;
                //数据预处理
                //---负载判断（佩戴检测）

                auto eegCheck = eegLoadCheck(eegRawData, deviceInfo);
                if (!eegCheck)
                {
                    res.eegWave = nc::zeros<double>(1,std::min(stepLen, dataLen));
                    res.featureWave = nc::zeros<double>(1,std::min(stepLen, dataLen));
                    tmp.eegData = nc::NdArray<double>();
                    
                    return res;
                }

                // ---跳点剔除
                auto outlierRawData = doubleOutlierRemove(eegRawData);

                // ---电压计算
                auto eegVoltData = voltageCal(outlierRawData, deviceInfo->eegMaxUv(),
                                             deviceInfo->eegMinUv(), deviceInfo->eegMaxVal(), deviceInfo->eegMinVal());

                // ---数据拼接
                nc::NdArray<double> eegVoltDataNc(eegVoltData);
                tmp.eegData = nc::hstack({tmp.eegData, eegVoltDataNc});
                if (tmp.eegData.size() < dataLen)
                {
                    res.eegWave = nc::zeros<double>(1,stepLen);
                    res.featureWave = nc::zeros<double>(1,stepLen);
                    return res;
                }
                if (tmp.eegData.size() > dataLen)
                    tmp.eegData = tmp.eegData[nc::Slice(tmp.eegData.size() - dataLen, tmp.eegData.size())];
                else 
                    tmp.eegData = tmp.eegData[nc::Slice(0, tmp.eegData.size())];


                //信号处理
                auto eegVec = tmp.eegData.toStlVector();
                auto noDriftWave = eegDriftFilter(eegVec);


                //带通滤波
                auto filterWave = eegLowpassFilter(noDriftWave);

                //伪迹去除
                auto noArtifactWave = eegArtifactRemove(filterWave);

                //小波去噪
                auto denoiseWaveSoft = eegWaveletDenoise(noArtifactWave.first, "db4", "soft");

                auto denoiseWaveHard = eegWaveletDenoise(noArtifactWave.first, "db4", "hard");

                // 信号参数计算
                // --信号质量判断
                auto eegQuality = eegQualityCal(filterWave, deviceInfo, true);


                // 波形选择
                // ---输出相关波形
                nc::NdArray<double> filterWaveNc(filterWave, false);

                auto eegWave = filterWaveNc[nc::Slice(filterWaveNc.size()-stepLen, filterWaveNc.size())];
                nc::NdArray<double> denoiseWaveHardNc(denoiseWaveHard, false);
                auto waveDenoiseLen = static_cast<size_t>(std::round(eegFs * epochLen));
                auto epochWave = denoiseWaveHardNc[nc::Slice(denoiseWaveHardNc.size()-waveDenoiseLen, denoiseWaveHardNc.size())].copy();

                // ---特征相关波形
                nc::NdArray<double> denoiseWaveSoftNc(denoiseWaveSoft, false);
                auto featureWave = denoiseWaveSoftNc[nc::Slice(denoiseWaveSoftNc.size()-stepLen, denoiseWaveSoftNc.size())];
                auto epochFeatureWave = denoiseWaveSoftNc[nc::Slice(denoiseWaveSoftNc.size()-waveDenoiseLen, denoiseWaveSoftNc.size())].copy();

                // 输出结果
                // ---根据信号质量输出不同结果
                auto vecEpochWave = epochWave.toStlVector();
                auto eegPower = eegPowerCal(vecEpochWave, deviceInfo);
                auto vecEpochFeatureWave = epochFeatureWave.toStlVector();
                auto featurePower = eegPowerCal(vecEpochFeatureWave, deviceInfo);

                res.quality = eegQuality;
                if (eegQuality == EEGQuality::GOOD || eegQuality == EEGQuality::POOR)
                {
                    res.eegWave = eegWave;
                    res.featureWave = featureWave;
                    res.power = eegPower;
                    res.featurePower = featurePower;
                }
                else
                {
                    res.eegWave = nc::zeros<double>(1,eegWave.size());
                    res.featureWave = nc::zeros<double>(1,featureWave.size());
                    EEGPower eegPowerTmp;
                    EEGPower featurePowerTmp;
                    res.power = eegPowerTmp;
                    res.featurePower = featurePowerTmp;
                }

                return res;

            }
        }
    } // namespace dsp

}