//
// Created by Enter M1 on 2023/9/12.
//

#include "WaveletTool.h"
#include <vector>
#include <string>


namespace basic::mathtool::wavelet
{
    C_L WaveDec(const std::vector<double>& signal,  const int nMaxLevel, const char* strWaveName)
    {
        const WaveFilter& filters = WFilters(strWaveName, 'd'); // ѡ��ֽ��˲���
        int len = signal.size();
        C_L cl;
        cl.L.push_back(len);
        WaveCoeff waveCoeff;
        waveCoeff.app = signal;
        std::vector<double>::iterator itC;
        std::vector<int>::iterator itL;

        for (int i = 0; i < nMaxLevel; ++i)
        {
            waveCoeff = DWT(waveCoeff.app, filters.Low, filters.High);
            itC = cl.C.begin();
            cl.C.insert(itC, waveCoeff.det.begin(), waveCoeff.det.end());
            itL = cl.L.begin();
            cl.L.insert(itL, waveCoeff.det.size());
        }
        itC = cl.C.begin();
        cl.C.insert(itC, waveCoeff.app.begin(), waveCoeff.app.end());
        itL = cl.L.begin();
        cl.L.insert(itL, waveCoeff.app.size());
        return cl;
    }

    std::vector<double> WRCoef(const char a_or_d, const std::vector<double>& C, const std::vector<int>& L,
                                   const char* strWaveName, const int nLevel)
    {
        std::vector<double> Coef;
        const WaveFilter& filter = WFilters(strWaveName, 'r'); // ѡ��С���ع��˲���
        int nMax = L.size() - 2;
        int nMin;
        char type = std::tolower(a_or_d);
        if ('a' == type) // a��ʾ�ع�����ϵ��
            nMin = 0;
        else if ('d' == type) // d��ʾ�ع�ϸ��ϵ��
            nMin = 1;
        else
        {
            return Coef;
        }
        if (nLevel < nMin || nLevel > nMax)
        {
            return Coef;
        }
        std::vector<double> F1;
        switch (type)
        {
            case 'a':
                Coef = AppCoef(C, L, strWaveName, nLevel);
                if (0 == nLevel)
                    return Coef;
                F1 = filter.Low;
                break;
            case 'd':
                Coef = DetCoef(C, L, nLevel);
                F1 = filter.High;
                break;
            default:
                ;
        }
        int iMin = L.size() - nLevel;
        Coef = UpsConv1(Coef, F1, L[iMin], "sym");
        for (int k = 1; k < nLevel; ++k)
        {
            Coef = UpsConv1(Coef, filter.Low, L[iMin + k], "sym");
        }
        return Coef;
    }

    std::vector<double> UpsConv1(const vector<double>& signal, const vector<double>& filter,
                                     const int nLen,  const char* strMode)
    {
        //implement dyadup(y,0)
        vector<double> y(2 * signal.size() - 1);
        y[0] = signal[0];

        // 2 ��ֵ
        for (int i = 1; i < signal.size(); ++i)
        {
            y[2*i - 1] = 0;
            y[2*i] = signal[i];
        }
        y = Conv(y, filter);

        //extract the central portion
        std::vector<double>::iterator it = y.begin();
        return std::vector<double>(it + (y.size() - nLen) / 2, it + (y.size() + nLen) / 2);
    }

    std::vector<double> Conv(const std::vector<double>& vecSignal, const std::vector<double>& vecFilter)
    {
        std::vector<double> signal(vecSignal);
        std::vector<double> filter(vecFilter);
        if (signal.size() < filter.size())
            signal.swap(filter);
        int lenSignal = signal.size();
        int lenFilter = filter.size();
        std::vector<double> result(lenSignal + lenFilter - 1);

        for (int i = 0; i < lenFilter; i++)
        {
            for (int j = 0; j <= i; j++)
                result[i] += signal[j] * filter[i - j];
        }
        for (int i = lenFilter; i < lenSignal; i++)
        {
            for (int j = 0; j <lenFilter; j++)
                result[i] += signal[i - j] * filter[j];
        }
        for (int i = lenSignal; i < lenSignal + lenFilter - 1; i++)
        {
            for (int j = i - lenSignal + 1; j < lenFilter; j++)
                result[i] += signal[i - j] * filter[j];
        }
        return result;
    }

    std::vector<double> DetCoef(const std::vector<double>& C, const std::vector<int>& L, const int nLevel )
    {
        if (nLevel < 1 || nLevel > L.size() - 2)
        {
            return {};
        }

        int nlast = 0, nfirst = 0;
        std::vector<int>::const_reverse_iterator it = L.rbegin();
        ++it;
        for (int i = 1; i < nLevel; ++i)
        {
            nlast += *it;
            ++it;
        }
        nfirst = nlast + *it;
        return std::vector<double>(C.end() - nfirst, C.end() - nlast);
    }

    WaveCoeff DWT(const std::vector<double>& signal, const std::vector<double>& Lo_D, const std::vector<double>& Hi_D)
    {
        int nLenExt = Lo_D.size() - 1;
        std::vector<double> y;
        y = WExtend(signal, nLenExt, "sym");
        std::vector<double> z;
        z = WConv1(y, Lo_D, "valid");
        WaveCoeff coeff;
        for (int i = 1; i < z.size(); i += 2)
        {
            coeff.app.push_back(z[i]);
        }
        z = WConv1(y, Hi_D, "valid");
        for (int i = 1; i < z.size(); i += 2)
        {
            coeff.det.push_back(z[i]);
        }
        return coeff;
    }

    const WaveFilter& WFilters(const char* strWaveName, const char d_or_r)
    {
        char type = std::tolower(d_or_r);
        if (!std::strcmp(strWaveName, "sym5"))
        {

            switch(type)
            {
                case 'd':
                    return sym5_d;
                case 'r':
                    return sym5_r;
                default:
                    throw std::invalid_argument("type not support");;;
            }
        }
        else if (!strcmp(strWaveName, "db4"))
        {
            switch(type)
            {
                case 'd':
                    return db4_d;
                case 'r':
                    return db4_r;
                default:
                    throw std::invalid_argument("type not support");;;
            }
        }
        else
        {
            throw std::invalid_argument("type not support");;
        }
    }

    std::vector<double> AppCoef(const std::vector<double>& C, const std::vector<int>& L, const char* strWaveName, const int nLevel)
    {
        int nMaxLevel = L.size() - 2;
        if (nLevel < 0 || nLevel > nMaxLevel)
        {
            throw std::invalid_argument("bad parameter for level");;
        }
        const WaveFilter& filters = WFilters(strWaveName, 'r');
        std::vector<double> app(C.begin(), C.begin() + L[0]); //app for the last level
        std::vector<double> det;
        for (int i = 0; i < nMaxLevel - nLevel; ++i)
        {
            det = DetCoef(C, L, nMaxLevel - i);
            app = IDWT(app, det, filters.Low, filters.High, L[i + 2]);
        }
        return app;
    }

    std::vector<double> IDWT(const std::vector<double>& app,
                                 const std::vector<double>& det,
                                 const std::vector<double>& Lo_R,
                                 const std::vector<double>& Hi_R,
                                 const int nLenCentral)
    {
        std::vector<double> app1, app2;
        app1 = UpsConv1(app, Lo_R, nLenCentral, "sym");
        app2 = UpsConv1(det, Hi_R, nLenCentral, "sym");
        for (int i = 0; i < nLenCentral; ++i)
        {
            app1[i] += app2[i];
        }
        return app1;
    }

    std::vector<double> WExtend(const std::vector<double>& signal, const int nLenExt, const char* mode)
    {
        int signalLen = signal.size();
        std::vector<double> result(signalLen + 2 * nLenExt);
        for (int i = 0, idx = nLenExt; idx < signalLen + nLenExt; ++i, ++idx)
        {
            result[idx] = signal[i];
        }
        for (int idx = nLenExt - 1, bFlag = 1, signalIdx = 0; idx >= 0; --idx)
        {
            result[idx] = signal[signalIdx];
            if (bFlag && ++signalIdx == signalLen)
            {
                bFlag = 0;
                signalIdx = signalLen - 1;
            }
            else if (!bFlag && --signalIdx == -1)
            {
                bFlag = 1;
                signalIdx = 0;
            }
        }
        for (int idx = nLenExt + signalLen, bFlag = 0, signalIdx = signalLen - 1; idx < 2 * nLenExt + signalLen; ++idx)
        {
            result[idx] = signal[signalIdx];
            if (bFlag && ++signalIdx == signalLen)
            {
                bFlag = 0;
                signalIdx = signalLen - 1;
            }
            else if (!bFlag && --signalIdx == -1)
            {
                bFlag = 1;
                signalIdx = 0;
            }
        }
        return result;
    }

    std::vector<double> WConv1(const std::vector<double>& signal, const std::vector<double>& filter, const char* shape)
    {
        std::vector<double> y;
        y = Conv(signal, filter);
        int nLenExt = filter.size() - 1;
        return std::vector<double>(y.begin() + nLenExt, y.end() - nLenExt);
    }

}
